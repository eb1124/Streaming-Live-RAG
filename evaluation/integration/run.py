"""End-to-end integration evaluation: the complete pipeline, every stage recorded.

  python -m evaluation.integration.run --runs 2

Pipeline per case (existing components, called through their public methods, nothing re-parameterized):
  hybrid.fuse(query)                      dense (arctic-m) + BM25, RRF            retrieval.hybrid
  resolver.resolve(query, fused ids)      temporal / version resolution           temporal.resolve
  reranker.rerank(query, kept[:20])       cross-encoder, pool = CANDIDATE_POOL    retrieval.rerank
  top RETRIEVE_K (10) -> Evidence         with rerank scores                      generation.pipeline
  GroundedAnswerer(provider).answer()     gate, context, Groq, verification       generation.answer
The retrieval stages and the answer call are generation.pipeline.retrieve / answer_retrieval, the same
functions the CLI (python -m generation) uses; the harness only records what they return.

The harness adds three things around the (unchanged) Groq provider: it records each call's latency, token
usage and errors; it waits MIN_CALL_INTERVAL_S between calls to reduce rate-limit errors; and on HTTP 429
it retries a bounded number of times (RATE_LIMIT_BACKOFF_S). Waits are not counted in any latency.
A case whose call still fails has no model output: the run is marked INCOMPLETE in summary.md (such
cases are counted separately, not as model results) and the exit code is 3.

Writes results/run-<n>.json (everything recorded) and results/summary.md (automatic checks only).
The automatic checks are NOT a judgment of answer correctness; see results/report.md for manual review.
Exit code 2 if GROQ_API_KEY is missing (nothing is sent), 3 if any case has a provider failure,
1 if any automatic check fails, else 0.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from .cases import CASES, resolve

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
POOL_REPORT = 20  # candidates recorded per stage
MIN_CALL_INTERVAL_S = 3.0
# HTTP 429 (Groq on-demand tokens-per-minute window is 60 s) after the SDK's own retries: at most
# len(RATE_LIMIT_BACKOFF_S) further attempts, each after max(backoff, Retry-After) capped at RATE_LIMIT_MAX_WAIT_S.
RATE_LIMIT_BACKOFF_S = (15.0, 30.0, 60.0)
RATE_LIMIT_MAX_WAIT_S = 60.0
STAGES = ["provider", "retrieval", "temporal", "reranking", "context", "generation", "verification", "citation"]


class RecordingProvider:
    """Wraps the configured provider: records latency / usage / errors per call; paces calls."""

    def __init__(self, inner, min_interval: float = MIN_CALL_INTERVAL_S):
        self.inner, self.min_interval = inner, min_interval
        self.name, self.model = inner.name, inner.model
        self.settings = getattr(inner, "settings", {})
        self.last: dict | None = None
        self._last_end = 0.0

    def complete(self, system, user, schema):
        from generation.providers import ProviderError

        wait = max(self.min_interval - (time.monotonic() - self._last_end), 0.0)
        time.sleep(wait)
        rec = {"system_chars": len(system), "user_message": user, "paced_wait_s": round(wait, 3),
               "rate_limit_retries": [], "rate_limit_wait_s": 0.0}
        t = time.perf_counter()
        while True:
            try:
                resp = self.inner.complete(system, user, schema)
                break
            except ProviderError as e:
                retries = rec["rate_limit_retries"]
                if not e.rate_limited or len(retries) >= len(RATE_LIMIT_BACKOFF_S):
                    self._fail(rec, t, e)
                    raise
                backoff = min(max(RATE_LIMIT_BACKOFF_S[len(retries)], e.retry_after or 0.0), RATE_LIMIT_MAX_WAIT_S)
                retries.append({"wait_s": backoff, "retry_after": e.retry_after, "error": str(e)[:200]})
                rec["rate_limit_wait_s"] += backoff
                time.sleep(backoff)
            except Exception as e:
                self._fail(rec, t, e)
                raise
        rec.update(seconds=round(time.perf_counter() - t - rec["rate_limit_wait_s"], 3), error=None, usage=resp.usage,
                   model=resp.model, finish_reason=resp.finish_reason, system_fingerprint=resp.system_fingerprint)
        self.last = rec
        self._last_end = time.monotonic()
        return resp

    def _fail(self, rec: dict, t: float, e: Exception) -> None:
        rec.update(seconds=round(time.perf_counter() - t - rec["rate_limit_wait_s"], 3), error=f"{type(e).__name__}: {e}")
        self.last = rec
        self._last_end = time.monotonic()


def rank_of(ids: list[str], unit: set[str]) -> int | None:
    return min((ids.index(c) + 1 for c in unit if c in ids), default=None)


def run_case(case: dict, stack, provider: RecordingProvider, env: dict) -> dict:
    from generation.context import assemble, render_sources
    from generation.pipeline import answer_retrieval, retrieve

    chunks, org_of = env["chunks"], env["org_of"]
    q = case["question"]

    r = retrieve(stack, q)  # the same integrated retrieval the CLI (generation.pipeline.ask) uses
    lat = dict(r.seconds)
    fused, res, rr, evidence = r.fused, r.resolution, r.reranked, r.evidence
    fused_ids = [chunks[f.index].chunk_id for f in fused]
    pool_ids = [chunks[i].chunk_id for i in r.pool]
    rr_ids = [chunks[x.index].chunk_id for x in rr]

    t = time.perf_counter()
    ctx = assemble(evidence)  # identical to what the answerer assembles internally (deterministic)
    lat["context_assembly"] = time.perf_counter() - t
    rendered = render_sources(ctx)

    provider.last = None
    t = time.perf_counter()
    answer = answer_retrieval(provider, r)
    answer_s = time.perf_counter() - t
    call = provider.last
    llm_s = call["seconds"] if call else 0.0
    lat["llm_call"] = llm_s
    lat["gate_assembly_verification"] = max(
        answer_s - llm_s - (call or {}).get("paced_wait_s", 0.0) - (call or {}).get("rate_limit_wait_s", 0.0), 0.0)
    lat["total_excl_pacing"] = sum(lat.values())
    lat = {k: round(v, 3) for k, v in lat.items()}

    by_f = {chunks[f.index].chunk_id: f for f in fused}
    record = {
        "id": case["id"], "tags": case["tags"], "question": q, "expect": case["expect"],
        "retrieved_candidates": [
            {"rank": i, "chunk_id": cid, "org": org_of[cid], "rrf": round(by_f[cid].score, 6),
             "dense_rank": by_f[cid].ranks.get("dense"), "bm25_rank": by_f[cid].ranks.get("bm25")}
            for i, cid in enumerate(fused_ids[:POOL_REPORT], 1)],
        "temporal": {"intent": res.intent.kind, "trigger": res.intent.trigger, "selected": res.selected,
                     "flags": res.flags, "dropped_total": len(res.dropped),
                     "dropped_from_top20": [c for c in fused_ids[:POOL_REPORT] if c in set(res.dropped)],
                     "rerank_pool": pool_ids},
        "reranked_candidates": [
            {"rank": i, "chunk_id": chunks[r.index].chunk_id, "org": org_of[chunks[r.index].chunk_id],
             "score": round(r.score, 4), "pool_rank": r.hybrid_rank} for i, r in enumerate(rr, 1)],
        "context": {"sources": [{"label": s.label, "chunk_id": s.chunk.chunk_id, "org": s.chunk.organization,
                                 "retrieval_rank": s.retrieval_rank, "duplicate_of": s.duplicate_of} for s in ctx.sources],
                    "skipped": ctx.skipped, "source_tokens": ctx.source_tokens, "rendered": rendered},
        "answer": answer.to_dict(), "rendered_answer": answer.render(),
        "provider_call": None if call is None else {k: v for k, v in call.items() if k != "user_message"},
        "latency_s": lat,
    }
    record["checks"] = check(case, record, fused_ids, res, rr_ids, env)
    return record


def check(case: dict, rec: dict, fused_ids, res, rr_ids, env) -> dict:
    from evaluation.temporal.cases import SERIES, VERSION_DOC

    cbd, doc_of, org_of = env["chunks_by_doc"], env["doc_of"], env["org_of"]
    units = [{resolve(r, cbd) for r in u} for u in case["gold"]]
    cite_units = [{resolve(r, cbd) for r in u} for u in case["cite"]]
    forbidden = {resolve(r, cbd) for r in case["forbidden"]}
    pool = rec["temporal"]["rerank_pool"]
    top10 = rr_ids[:10]
    ctx_ids = [s["chunk_id"] for s in rec["context"]["sources"]]
    a = rec["answer"]
    cited = [c["chunk_id"] for c in a["citations"]]
    status = "answer" if a["status"] == "answered" else "abstain"

    # A. retrieval / evidence
    gold = [{"hybrid_rank": rank_of(fused_ids, u), "dropped_by_temporal": bool(u & set(res.dropped)) and not (u & set(res.kept)),
             "pool_rank": rank_of(pool, u), "rerank_rank": rank_of(rr_ids, u), "in_top10": rank_of(top10, u) is not None,
             "in_context": bool(u & set(ctx_ids))} for u in units]
    # B. temporal
    exp = sorted(VERSION_DOC[v] for v in case["versions"]) if case["versions"] is not None else None
    sel = res.selected.get(SERIES)
    series_docs = set(VERSION_DOC.values())
    wrong_docs = series_docs - set(exp) if exp is not None else set()
    temporal = {"intent_ok": res.intent.kind == case["intent"],
                "selection_ok": (sel is None) if exp is None else (sel == exp),
                "wrong_version_in_pool": sum(doc_of[c] in wrong_docs for c in pool),
                "wrong_version_in_context": sum(doc_of[c] in wrong_docs for c in ctx_ids),
                "forbidden_in_context": sorted(forbidden & set(ctx_ids))}  # may include wrong-org distractors (not a failure)
    # a forbidden UConn-procedures chunk is a temporal failure only when the case resolves the series
    # (in version-neutral cases it is a wrong-organization distractor, checked via citations)
    wrong_version_forbidden = [c for c in temporal["forbidden_in_context"]
                               if doc_of[c] in series_docs and case["versions"] is not None]
    # C. reranking vs hybrid agreement (post-temporal order vs reranked order)
    agree = {"top1_same": bool(pool and rr_ids and pool[0] == rr_ids[0]),
             "overlap_at_10": len(set(pool[:10]) & set(top10)),
             "gold_pool_vs_rerank": [(g["pool_rank"], g["rerank_rank"]) for g in gold]}
    # D. citations
    citation = {"cited_outside_context": [c for c in cited if c not in ctx_ids],
                "missing_required": [i for i, u in enumerate(cite_units) if not (u & set(cited))] if status == "answer" else [],
                "forbidden_cited": sorted(forbidden & set(cited)),
                "wrong_org_cited": sorted({org_of[c] for c in cited} - set(case["orgs"] or []))}
    # E. explicit expected-answer check (answer text only; not a correctness verdict)
    text = a["text"] if status == "answer" else ""
    facts_missing = [f for f in case["facts"] if not re.search(f, text, re.IGNORECASE)] if status == "answer" else []
    wrong_found = [w for w in case["wrong"] if re.search(w, text, re.IGNORECASE)]

    fails: list[tuple[str, str]] = []
    reason = a["abstention_reason"]
    if reason == "provider_error":
        fails.append(("provider", a["abstention_detail"][:200]))
    for i, g in enumerate(gold if case["expect"] == "answer" else []):  # abstain cases: gold ranks recorded only
        if g["pool_rank"] is None:
            fails.append(("temporal", f"gold unit {i} removed by temporal resolution") if g["dropped_by_temporal"]
                         else ("retrieval", f"gold unit {i} not in the 20-candidate rerank pool (hybrid rank {g['hybrid_rank']})"))
        elif not g["in_top10"]:
            fails.append(("reranking", f"gold unit {i} at pool rank {g['pool_rank']} dropped to rerank rank {g['rerank_rank']}"))
        elif not g["in_context"]:
            fails.append(("context", f"gold unit {i} in top 10 but not in the assembled context"))
    if not temporal["intent_ok"] or not temporal["selection_ok"]:
        fails.append(("temporal", f"intent {res.intent.kind} / selected {sel} (expected {case['intent']} / {exp})"))
    if temporal["wrong_version_in_context"] or wrong_version_forbidden:
        fails.append(("temporal", "wrong-version chunk in the context"))
    if case["expect"] == "answer" and status == "abstain" and reason != "provider_error":
        stage = {"low_relevance": "reranking", "no_evidence": "context", "ungrounded_output": "verification"}.get(reason, "generation")
        fails.append((stage, f"abstained ({reason}): {a['abstention_detail'][:200]}"))
    if case["expect"] == "abstain" and status == "answer":
        fails.append(("generation", "answered a question that should be abstained"))
    if citation["cited_outside_context"] or citation["missing_required"] or citation["forbidden_cited"] or citation["wrong_org_cited"]:
        fails.append(("citation", json.dumps({k: v for k, v in citation.items() if v})))
    if facts_missing or wrong_found:
        fails.append(("generation", f"explicit answer check: missing {facts_missing} / misstatement {wrong_found}"))
    order = {s: i for i, s in enumerate(STAGES)}
    primary = min((s for s, _ in fails), key=order.get, default=None)
    return {"status": status, "status_ok": status == case["expect"], "abstention_reason": reason,
            "verifier_rejected": reason == "ungrounded_output",
            "gold": gold, "temporal": temporal, "rerank_agreement": agree, "citation": citation,
            "facts_missing": facts_missing, "wrong_found": wrong_found,
            "passed": not fails, "failures": [{"stage": s, "detail": d} for s, d in fails], "primary_stage": primary}


def run_once(n: int, stack, provider: RecordingProvider, env: dict) -> list[dict]:
    out = []
    for case in CASES:
        rec = run_case(case, stack, provider, env)
        out.append(rec)
        c = rec["checks"]
        print(f"run {n} {case['id']:20} {'PASS' if c['passed'] else 'FAIL':4} {c['status']:7} "
              f"{rec['latency_s']['total_excl_pacing']:6.1f}s {'; '.join(f['stage'] + ': ' + f['detail'] for f in c['failures'])[:150]}",
              flush=True)
    return out


def agreement(runs: list[list[dict]]) -> list[dict]:
    rows = []
    for i, case in enumerate(CASES):
        rs = [r[i] for r in runs]
        same = lambda f: len({json.dumps(f(r), sort_keys=True) for r in rs}) == 1  # noqa: E731
        rows.append({
            "id": case["id"],
            "same_retrieval": same(lambda r: [c["chunk_id"] for c in r["retrieved_candidates"]]),
            "same_temporal": same(lambda r: r["temporal"]["rerank_pool"]),
            "same_rerank": same(lambda r: [c["chunk_id"] for c in r["reranked_candidates"]]),
            "same_context": same(lambda r: [s["chunk_id"] for s in r["context"]["sources"]]),
            "same_status": same(lambda r: r["answer"]["status"]),
            "same_abstention_reason": same(lambda r: r["answer"]["abstention_reason"]),
            "same_citations": same(lambda r: sorted(c["chunk_id"] for c in r["answer"]["citations"])),
            "same_explicit_check": same(lambda r: (r["checks"]["facts_missing"], r["checks"]["wrong_found"])),
            "identical_text": same(lambda r: r["answer"]["text"]),
        })
    return rows


def provider_failures(run: list[dict]) -> list[str]:
    return [r["id"] for r in run if r["answer"]["abstention_reason"] == "provider_error"]


def fmt_gold(gold: list[dict]) -> str:
    return "; ".join(f"{g['hybrid_rank'] or '—'}→{g['pool_rank'] or '—'}→{g['rerank_rank'] or '—'}{'' if g['in_context'] else '✗ctx'}"
                     for g in gold) or "—"


def write_summary(runs: list[list[dict]], meta: dict) -> str:
    n = len(CASES)
    out = ["# Integration suite: automatic checks", "",
           f"Provider {meta['provider']}, model {meta['model']}, prompt {meta['prompt_version']}, settings {meta['settings']}. "
           f"{n} cases × {len(runs)} runs. Automatic checks only (retrieval ranks, temporal selection, citations, status, "
           "regex fact checks). They are not a verdict on answer correctness; see report.md for the manual review.", "",
           "Gold column: per evidence unit, hybrid rank → rank in the post-temporal rerank pool → rerank rank (✗ctx = not in context).", ""]
    for k, run in enumerate(runs, 1):
        failed = provider_failures(run)
        out += [f"## Run {k}: {sum(r['checks']['passed'] for r in run)}/{n} passed all automatic checks", ""]
        if failed:
            out += [f"**INCOMPLETE:** {len(failed)} case(s) have no model output after bounded rate-limit retries "
                    f"({', '.join(failed)}). They are provider failures, not model results.", ""]
        out += [
                "| case | expect | got | intent / selected | gold ranks | cited | primary failing stage | failures |",
                "|---|---|---|---|---|---|---|---|"]
        for r in run:
            c, t = r["checks"], r["temporal"]
            sel = ",".join("feb" if "final" in d else "jul" for d in t["selected"].get("uconn-travel-entertainment-procedures", [])) \
                if "uconn-travel-entertainment-procedures" in t["selected"] else "—"
            got = c["status"] + (f" ({c['abstention_reason']})" if c["abstention_reason"] else "")
            cited = ", ".join(x["chunk_id"].split("::")[0][:14] + "…" + x["chunk_id"][-6:] for x in r["answer"]["citations"]) or "—"
            out.append(f"| {r['id']} | {r['expect']} | {got} | {t['intent']} / {sel or 'none'} | {fmt_gold(c['gold'])} | {cited} | "
                       f"{c['primary_stage'] or ''} | {'; '.join(f['stage'] + ': ' + f['detail'] for f in c['failures'])[:300]} |")
        lat = [r["latency_s"] for r in run]
        calls = [r["provider_call"] for r in run if r["provider_call"]]
        tok = [cl["usage"] for cl in calls if cl.get("usage")]
        out += ["", f"LLM calls: {len(calls)} (errors: {sum(1 for cl in calls if cl['error'])}); "
                f"tokens: prompt {sum(u.get('prompt_tokens', 0) for u in tok)}, completion {sum(u.get('completion_tokens', 0) for u in tok)}, "
                f"total {sum(u.get('total_tokens', 0) for u in tok)}; end-to-end latency excl. pacing: "
                f"total {sum(l['total_excl_pacing'] for l in lat):.1f} s.", ""]
    if len(runs) > 1:
        ag = agreement(runs)
        keys = [k for k in ag[0] if k != "id"]
        out += ["## Run-to-run agreement", "", " · ".join(f"{k}: {sum(r[k] for r in ag)}/{n}" for k in keys), ""]
    report = "\n".join(out) + "\n"
    (RESULTS / "summary.md").write_text(report, encoding="utf-8")
    return report


def recheck() -> int:
    """Recompute the automatic checks for the saved runs (no LLM calls). Retrieval, temporal resolution and
    reranking are deterministic (verified run to run), so they are re-derived; answers come from run-<n>.json."""
    from chunking.pipeline import load_chunks
    from generation import config as C
    from generation.pipeline import load_stack
    from generation.prompt import PROMPT_VERSION

    stack = load_stack(rerank=True)
    env = {"chunks_by_doc": load_chunks(), "chunks": stack.chunks, "doc_of": {c.chunk_id: c.doc_id for c in stack.chunks},
           "org_of": {c.chunk_id: c.organization for c in stack.chunks}}
    resolver = stack.resolver
    runs = []
    for path in sorted(RESULTS.glob("run-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for case, rec in zip(CASES, data["cases"]):
            assert case["id"] == rec["id"]
            fused_ids = [stack.chunks[f.index].chunk_id for f in stack.hybrid.fuse(case["question"])]
            assert [c["chunk_id"] for c in rec["retrieved_candidates"]] == fused_ids[:POOL_REPORT], rec["id"]
            res = resolver.resolve(case["question"], fused_ids)
            rr_ids = [c["chunk_id"] for c in rec["reranked_candidates"]]
            rec["checks"] = check(case, rec, fused_ids, res, rr_ids, env)
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        runs.append(data["cases"])
    meta = {"provider": "groq", "model": C.GROQ_MODEL, "prompt_version": PROMPT_VERSION,
            "settings": {"temperature": C.TEMPERATURE, "seed": C.SEED, "reasoning_effort": C.REASONING_EFFORT,
                         "max_completion_tokens": C.MAX_COMPLETION_TOKENS}}
    if len(runs) > 1:
        (RESULTS / "agreement.json").write_text(json.dumps(agreement(runs), indent=1) + "\n", encoding="utf-8")
    print(write_summary(runs, meta))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--recheck", action="store_true", help="recompute checks for saved runs; no LLM calls")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    if args.recheck:
        return recheck()
    from generation.env import load_env

    load_env()
    if not os.environ.get("GROQ_API_KEY"):
        print("BLOCKED: GROQ_API_KEY is not set (environment or .env); no request was sent.")
        return 2
    from chunking.pipeline import load_chunks
    from generation import config as C
    from generation.pipeline import load_stack
    from generation.prompt import PROMPT_VERSION
    from generation.providers import get_provider

    chunks_by_doc = load_chunks()
    stack = load_stack(rerank=True)
    env = {"chunks_by_doc": chunks_by_doc, "chunks": stack.chunks,
           "doc_of": {c.chunk_id: c.doc_id for c in stack.chunks},
           "org_of": {c.chunk_id: c.organization for c in stack.chunks}}
    provider = RecordingProvider(get_provider("groq"))
    RESULTS.mkdir(exist_ok=True)
    runs = []
    for n in range(1, args.runs + 1):
        started = time.strftime("%Y-%m-%dT%H:%M:%S")
        run = run_once(n, stack, provider, env)
        (RESULTS / f"run-{n}.json").write_text(json.dumps({"started": started, "cases": run}, indent=1, ensure_ascii=False) + "\n",
                                               encoding="utf-8")
        runs.append(run)
    meta = {"provider": provider.name, "model": C.GROQ_MODEL, "prompt_version": PROMPT_VERSION, "settings": provider.settings}
    if len(runs) > 1:
        (RESULTS / "agreement.json").write_text(json.dumps(agreement(runs), indent=1) + "\n", encoding="utf-8")
    print(write_summary(runs, meta))
    if any(provider_failures(run) for run in runs):
        return 3
    return 0 if all(r["checks"]["passed"] for run in runs for r in run) else 1


if __name__ == "__main__":
    raise SystemExit(main())
