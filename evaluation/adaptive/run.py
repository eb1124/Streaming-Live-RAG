"""Adaptive controller evaluation: the 27 integration cases through adaptive.controller, compared with the baseline.

  python -m evaluation.adaptive.run --runs 2

Same cases (evaluation/integration/cases.py), automatic checks (evaluation.integration.run.check), provider
pacing / bounded 429 retries (RecordingProvider) and run-to-run agreement as the integration evaluation. The
only difference is the answer step: AdaptiveController.answer(retrieve(...)) instead of answer_retrieval.

Writes results/run-<n>.json, results/agreement.json and results/summary.md in evaluation/adaptive/results/,
and compares each case with the frozen baseline in evaluation/integration/results/ (tag rag-baseline-2026-09-28),
which is only read. For split cases, "context" is every source shown to the model (single attempt and parts),
and "split_diagnostics" records each version part's outcome and citations (see split_diagnostics).

Persistence: run-<n>.json is rewritten after every completed case ({"started", "complete": false, "cases": [...]},
atomically: temporary file + rename) and marked "complete": true after the last case. If the process stops
(interrupt, crash, rate limit), the cases completed so far remain in run-<n>.json with "complete": false.
summary.md and agreement.json are written only when every run finishes; `--summarize` rewrites summary.md
from the saved run files, marking incomplete runs as such (agreement only when all runs are complete).
Exit code 2 if GROQ_API_KEY is missing, 3 if any case has a provider failure, 1 if any check fails, else 0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from evaluation.integration.cases import CASES
from evaluation.integration.run import POOL_REPORT, RecordingProvider, agreement, check, fmt_gold, provider_failures

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
BASELINE = HERE.parent / "integration" / "results"


class AllCallsProvider(RecordingProvider):
    """RecordingProvider that keeps every call of a case (a split case makes several)."""

    def __init__(self, inner):
        super().__init__(inner)
        self.calls: list[dict] = []

    def complete(self, system, user, schema):
        try:
            return super().complete(system, user, schema)
        finally:
            self.calls.append({k: v for k, v in (self.last or {}).items() if k != "user_message"})


def shown_contexts(controller, r, answer) -> list:
    """Every source the model was shown for this case, in first-shown order (single context, then parts)."""
    from adaptive.controller import SPLIT, evidence_for_version
    from generation.context import assemble

    ctxs = [assemble(r.evidence)]
    if answer.decision.strategy == SPLIT:
        ctxs += [assemble(evidence_for_version(r.evidence, p["series_id"], p["doc_id"])) for p in answer.parts]
    seen, out = set(), []
    for ctx in ctxs:
        for s in ctx.sources:
            if s.chunk.chunk_id not in seen:
                seen.add(s.chunk.chunk_id)
                out.append(s)
    return out


def run_case(case: dict, controller, provider: AllCallsProvider, env: dict) -> dict:
    from generation.pipeline import retrieve

    chunks, org_of = env["chunks"], env["org_of"]
    q = case["question"]
    r = retrieve(controller.stack, q)
    fused_ids = [chunks[f.index].chunk_id for f in r.fused]
    rr_ids = [chunks[x.index].chunk_id for x in r.reranked]
    provider.calls = []
    t = time.perf_counter()
    answer = controller.answer(r)
    answer_s = time.perf_counter() - t
    calls = provider.calls
    waits = sum(c.get("paced_wait_s", 0.0) + c.get("rate_limit_wait_s", 0.0) for c in calls)
    lat = dict(r.seconds) | {"llm_calls": sum(c.get("seconds", 0.0) for c in calls),
                             "controller_excl_llm_and_pacing": max(answer_s - waits - sum(c.get("seconds", 0.0) for c in calls), 0.0)}
    lat["total_excl_pacing"] = sum(lat.values())
    shown = shown_contexts(controller, r, answer)
    by_f = {chunks[f.index].chunk_id: f for f in r.fused}
    rec = {
        "id": case["id"], "tags": case["tags"], "question": q, "expect": case["expect"],
        "retrieved_candidates": [{"rank": i, "chunk_id": cid, "org": org_of[cid], "rrf": round(by_f[cid].score, 6)}
                                 for i, cid in enumerate(fused_ids[:POOL_REPORT], 1)],
        "temporal": {"intent": r.resolution.intent.kind, "trigger": r.resolution.intent.trigger,
                     "selected": r.resolution.selected, "flags": r.resolution.flags,
                     "rerank_pool": [chunks[i].chunk_id for i in r.pool]},
        "reranked_candidates": [{"rank": i, "chunk_id": chunks[x.index].chunk_id, "score": round(x.score, 4)}
                                for i, x in enumerate(r.reranked, 1)],
        "context": {"sources": [{"label": s.label, "chunk_id": s.chunk.chunk_id, "org": s.chunk.organization,
                                 "retrieval_rank": s.retrieval_rank, "duplicate_of": s.duplicate_of} for s in shown]},
        "answer": answer.to_dict(), "rendered_answer": answer.render(),
        "provider_calls": calls, "latency_s": {k: round(v, 3) for k, v in lat.items()},
    }
    rec["split_diagnostics"] = split_diagnostics(rec["answer"])
    rec["checks"] = check(case, rec, fused_ids, r.resolution, rr_ids, env)
    return rec


def split_diagnostics(answer: dict) -> dict | None:
    """For a split_by_version answer (AdaptiveAnswer.to_dict()): each version part's outcome and citations, and
    whether the combined answer kept every part's citations. None for the single strategy. Derived only from
    the recorded answer (the parts' raw model outputs stay in answer["parts"][i]["answer"]["raw_output"])."""
    if answer["decision"]["strategy"] != "split_by_version":
        return None
    final = [c["chunk_id"] for c in answer["citations"]]
    parts = []
    for p in answer["parts"]:
        a = p["answer"]
        cited = [c["chunk_id"] for c in a["citations"]]
        parts.append({
            "doc_id": p["doc_id"], "label": p["label"], "status": a["status"],
            "abstention_reason": a["abstention_reason"], "abstention_detail": a["abstention_detail"],
            "verification_problems": a["verification_problems"],
            "citations": [{"number": c["number"], "label": c["label"], "chunk_id": c["chunk_id"], "doc_id": c["doc_id"]}
                          for c in a["citations"]],
            "cited_chunk_ids": cited,
            "sources_shown": [s["chunk_id"] for s in a["sources_considered"]],
            "retained_in_final": all(c in final for c in cited),
        })
    return {"final_status": answer["status"], "final_cited_chunk_ids": final,
            "parts_answered": sum(p["status"] == "answered" for p in parts),
            "parts_abstained": sum(p["status"] != "answered" for p in parts),
            "every_part_citation_retained": all(p["retained_in_final"] for p in parts),
            "parts": parts}


def save_run(path: Path, started: str, cases: list[dict], complete: bool) -> None:
    """Write run-<n>.json atomically (temporary file + rename): an interruption never leaves a truncated file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"started": started, "complete": complete, "cases_total": len(CASES), "cases": cases},
                              indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run_once(n: int, controller, provider, env) -> dict:
    """Run every case; run-<n>.json is rewritten after each completed case and marked complete at the end."""
    path = RESULTS / f"run-{n}.json"
    run = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "complete": False, "cases": []}
    for case in CASES:
        rec = run_case(case, controller, provider, env)
        run["cases"].append(rec)
        save_run(path, run["started"], run["cases"], complete=False)
        c, diag = rec["checks"], rec["split_diagnostics"]
        parts = (" parts=" + ",".join(f"{p['doc_id'][:12]}:{p['status']}" for p in diag["parts"])) if diag else ""
        print(f"run {n} {case['id']:20} {'PASS' if c['passed'] else 'FAIL':4} {c['status']:7} "
              f"{rec['answer']['decision']['strategy']:16} calls={len(rec['provider_calls'])}{parts} "
              f"{'; '.join(f['stage'] + ': ' + f['detail'] for f in c['failures'])[:150]}", flush=True)
    run["complete"] = True
    save_run(path, run["started"], run["cases"], complete=True)
    return run


def load_runs() -> list[dict]:
    """Saved run files in run order (run-1.json, run-2.json, ...), complete or not."""
    runs, n = [], 1
    while (RESULTS / f"run-{n}.json").exists():
        data = json.loads((RESULTS / f"run-{n}.json").read_text(encoding="utf-8"))
        data.setdefault("complete", len(data["cases"]) == len(CASES))  # files written before incremental saving
        runs.append(data)
        n += 1
    return runs


def baseline_passed(n: int) -> dict[str, bool]:
    path = BASELINE / f"run-{n}.json"
    if not path.exists():
        return {}
    return {r["id"]: r["checks"]["passed"] for r in json.loads(path.read_text(encoding="utf-8"))["cases"]}


def write_summary(runs: list[dict], meta: dict) -> str:
    """runs: saved run dicts ({"started", "complete", "cases"}). An incomplete run is reported as such, with the
    count of completed cases only; run-to-run agreement is computed only when every run is complete."""
    n = len(CASES)
    out = ["# Adaptive controller: automatic checks vs. the frozen baseline", "",
           f"Provider {meta['provider']}, model {meta['model']}, prompt {meta['prompt_version']}. {n} cases × {len(runs)} runs "
           "(evaluation/integration/cases.py). Checks: evaluation.integration.run.check (automatic only; not a verdict "
           "on answer correctness). Baseline: evaluation/integration/results/ (tag rag-baseline-2026-09-28).", ""]
    for k, data in enumerate(runs, 1):
        run = data["cases"]
        base = baseline_passed(k)
        failed = provider_failures(run)
        split = [r["id"] for r in run if r["answer"]["decision"]["strategy"] == "split_by_version"]
        passed = sum(r["checks"]["passed"] for r in run)
        if data["complete"]:
            head = f"## Run {k}: {passed}/{n} passed (baseline run {k}: {sum(base.values())}/{n})"
        else:
            head = (f"## Run {k}: INCOMPLETE, {len(run)} of {n} cases completed; {passed}/{len(run)} completed cases passed "
                    "(no run total)")
        out += [f"{head}; split_by_version: {len(split)} ({', '.join(split) or 'none'})", ""]
        if failed:
            out += [f"**INCOMPLETE:** provider failures in {', '.join(failed)} (not model results).", ""]
        out += ["| case | expect | got | strategy | LLM calls | baseline | adaptive | gold ranks | failures | decision reason |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        for r in run:
            c, d = r["checks"], r["answer"]["decision"]
            got = c["status"] + (f" ({c['abstention_reason']})" if c["abstention_reason"] else "")
            b = {True: "pass", False: "fail"}.get(base.get(r["id"]), "—")
            out.append(f"| {r['id']} | {r['expect']} | {got} | {d['strategy']} | {len(r['provider_calls'])} | {b} | "
                       f"{'pass' if c['passed'] else 'fail'} | {fmt_gold(c['gold'])} | "
                       f"{'; '.join(f['stage'] + ': ' + f['detail'] for f in c['failures'])[:250]} | {d['reason']} |")
        calls = [c for r in run for c in r["provider_calls"]]
        tok = [c["usage"] for c in calls if c.get("usage")]
        out += ["", f"LLM calls: {len(calls)} (errors: {sum(1 for c in calls if c.get('error'))}); tokens total "
                f"{sum(u.get('total_tokens', 0) for u in tok)}.", ""]
    if len(runs) > 1 and all(d["complete"] for d in runs):
        ag = agreement([d["cases"] for d in runs])
        keys = [k for k in ag[0] if k != "id"]
        out += ["## Run-to-run agreement", "", " · ".join(f"{k}: {sum(r[k] for r in ag)}/{n}" for k in keys), ""]
    elif len(runs) > 1:
        out += ["## Run-to-run agreement", "", "Not computed: at least one run is incomplete.", ""]
    report = "\n".join(out) + "\n"
    (RESULTS / "summary.md").write_text(report, encoding="utf-8")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--summarize", action="store_true", help="rewrite summary.md from saved run files; no LLM calls")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from generation import config as C
    from generation.prompt import PROMPT_VERSION

    if args.summarize:
        print(write_summary(load_runs(), {"provider": "groq", "model": C.GROQ_MODEL, "prompt_version": PROMPT_VERSION}))
        return 0
    from generation.env import load_env

    load_env()
    if not os.environ.get("GROQ_API_KEY"):
        print("BLOCKED: GROQ_API_KEY is not set (environment or .env); no request was sent.")
        return 2
    from adaptive.controller import AdaptiveController
    from chunking.pipeline import load_chunks
    from generation.pipeline import load_stack
    from generation.providers import get_provider

    stack = load_stack(rerank=True)
    env = {"chunks_by_doc": load_chunks(), "chunks": stack.chunks, "doc_of": {c.chunk_id: c.doc_id for c in stack.chunks},
           "org_of": {c.chunk_id: c.organization for c in stack.chunks}}
    provider = AllCallsProvider(get_provider("groq"))
    controller = AdaptiveController(stack, provider)
    RESULTS.mkdir(exist_ok=True)
    runs = [run_once(n, controller, provider, env) for n in range(1, args.runs + 1)]  # each saved case by case
    if len(runs) > 1:
        (RESULTS / "agreement.json").write_text(json.dumps(agreement([d["cases"] for d in runs]), indent=1) + "\n",
                                                encoding="utf-8")
    meta = {"provider": provider.name, "model": C.GROQ_MODEL, "prompt_version": PROMPT_VERSION}
    print(write_summary(runs, meta))
    if any(provider_failures(d["cases"]) for d in runs):
        return 3
    return 1 if any(not r["checks"]["passed"] for d in runs for r in d["cases"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
