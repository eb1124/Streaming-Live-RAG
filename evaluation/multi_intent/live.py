"""Phase 2 live evaluation: the phase 2 cases (evaluation/multi_intent/cases.py) through the multi-intent controller
with the configured LLM, with phase 1 as the comparison where the two differ.

  python -m evaluation.multi_intent.live --run 1 [--token-budget N]   # live; resumes live-run-1.json if present
  python -m evaluation.multi_intent.live --summarize                  # rewrite live-summary.md; no LLM calls

Per case: phase 2 (MultiIntentController.run). Phase 1 (AdaptiveController.run) runs as well only when phase 2 did not
delegate. A delegated question's model input is byte-identical to phase 1's and the delegated answer IS phase 1's
answer (tests/test_multi_intent_controller.py), so no duplicate call is made and that answer is recorded as phase 1's.

Provider: evaluation.adaptive.run.AllCallsProvider, i.e. the integration RecordingProvider (3 s pacing, at most 3
bounded waits on HTTP 429, then the error is raised; the answerer turns it into a provider_error abstention).

Persistence and resume (results/live-run-<n>.json, rewritten atomically after every case):
  * a case is done when all its model calls succeeded; done cases are never re-run and their records never change
  * a case with a provider/API error is saved with the error and the run STOPS (after a daily token limit every
    following call would fail too). The same command resumes: done cases are skipped, the errored case is redone
  * --token-budget N stops cleanly before starting another case once this invocation has used N tokens
Only files named live-* under evaluation/multi_intent/results/ are written.
Exit: 0 complete, 2 GROQ_API_KEY missing, 3 stopped on a provider error, 4 stopped by the token budget.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
EXIT_OK, EXIT_NO_KEY, EXIT_PROVIDER_ERROR, EXIT_BUDGET = 0, 2, 3, 4


def _answer(a) -> dict:
    """The fields of a phase 1 / phase 2 answer that the evaluation records."""
    return {"status": a.status, "text": a.text, "citations": [asdict(c) for c in a.citations], "claims": a.claims,
            "abstention_reason": a.abstention_reason, "abstention_detail": a.abstention_detail,
            "verification_problems": a.verification_problems, "not_in_sources": a.not_in_sources,
            "verification_notes": a.verification_notes}


def _tokens(calls: list[dict]) -> int:
    return sum((c.get("usage") or {}).get("total_tokens", 0) for c in calls)


def _errors(calls: list[dict]) -> list[str]:
    return [c["error"] for c in calls if c.get("error")]


def run_case(case: dict, phase2, phase1, provider, chunks_by_doc: dict | None = None) -> dict:
    """One case: phase 2, then phase 1 unless phase 2 delegated. provider.calls is reset per phase."""
    from adaptive.multi.controller import DELEGATE

    q = case["question"]
    provider.calls = []
    a2 = phase2.run(q)
    calls2 = list(provider.calls)
    p2 = {"strategy": a2.strategy, "reason": a2.reason, "decomposition": a2.decomposition, "intents": a2.intents,
          "citation_intents": a2.citation_intents, **_answer(a2),
          "phase1_strategies": [x.decision.strategy for x in a2.phase1],
          "llm_calls": len(calls2), "tokens": _tokens(calls2), "calls": calls2}
    if _errors(calls2):  # the case is redone on resume; a comparison call now would only hit the same limit
        p1 = {"run": False, "source": "skipped: phase 2 provider error", "strategy": None, "status": None, "text": "",
              "citations": [], "claims": [], "abstention_reason": None, "abstention_detail": "",
              "verification_problems": [], "not_in_sources": [], "verification_notes": [], "llm_calls": 0, "tokens": 0,
              "calls": []}
    elif a2.strategy == DELEGATE:
        shared = a2.phase1[0]
        p1 = {"run": False, "source": "shared: phase 2 delegated (identical model input, no duplicate call)",
              "strategy": shared.decision.strategy, **_answer(shared), "llm_calls": 0, "tokens": 0, "calls": []}
    else:
        provider.calls = []
        a1 = phase1.run(q)
        calls1 = list(provider.calls)
        p1 = {"run": True, "source": "separate phase 1 run", "strategy": a1.decision.strategy, **_answer(a1),
              "llm_calls": len(calls1), "tokens": _tokens(calls1), "calls": calls1}
    errors = _errors(calls2) + _errors(p1["calls"])
    rec = {"id": case["id"], "tags": case["tags"], "question": q, "expected_multi": case["multi"],
           "expected_strategy": case["strategy"], "phase2": p2, "phase1": p1,
           "llm_calls": p2["llm_calls"] + p1["llm_calls"], "tokens": p2["tokens"] + p1["tokens"],
           "provider_errors": errors, "done": not errors, "finished": time.strftime("%Y-%m-%dT%H:%M:%S")}
    rec["checks"] = checks(case, rec)
    if chunks_by_doc is not None:
        rec["gold_cited"] = gold_cited(case, rec, chunks_by_doc)
    return rec


def checks(case: dict, rec: dict) -> list[str]:
    """Automatic checks on the phase 2 answer (not a verdict on correctness; see the texts for manual review)."""
    p2, out = rec["phase2"], []
    if rec["provider_errors"]:
        out.append("provider error: " + rec["provider_errors"][0][:160])
    if p2["decomposition"]["multi"] != case["multi"]:
        out.append(f"decomposition: multi={p2['decomposition']['multi']} expected {case['multi']}")
    if case["strategy"] and p2["strategy"] != case["strategy"]:
        out.append(f"strategy: {p2['strategy']} expected {case['strategy']}")
    if not case["multi"] or len(p2["intents"]) != len(case["intents"]):
        return out
    org_of = {c["number"]: c["organization"] for c in p2["citations"]}
    for intent, part in zip(p2["intents"], case["intents"]):
        k = intent["index"]
        claims = [cl for cl in p2["claims"] if k in cl["intents"]]
        if part["expect"] == "answer":
            if intent["status"] != "answered":
                out.append(f"intent {k + 1}: expected an answer, got {intent['status']}")
            else:
                text = " ".join(cl["text"] for cl in claims)
                missing = [f for f in part["facts"] if not re.search(f, text, re.I)]
                if missing:
                    out.append(f"intent {k + 1}: missing facts {missing}")
        elif intent["status"] == "answered":
            out.append(f"intent {k + 1}: answered, but the corpus cannot answer it")
        allowed = set(intent["organizations"]) | set(intent["carried"])
        cited = {org_of[n] for cl in claims for n in cl["citations"]}
        if allowed and not cited <= allowed:
            out.append(f"intent {k + 1}: cites other organizations {sorted(cited - allowed)}")
    return out


def gold_cited(case: dict, rec: dict, chunks_by_doc: dict) -> list[list[bool]]:
    """Per intent, per gold unit: whether phase 2 cites one of its chunks (information only)."""
    from evaluation.integration.cases import resolve

    cited = {c["chunk_id"] for c in rec["phase2"]["citations"]}
    return [[bool({resolve(r, chunks_by_doc) for r in unit} & cited) for unit in part["gold"]]
            for part in case["intents"]]


# ---------------------------------------------------------------------------------------------- persistence


def run_path(results: Path, n: int) -> Path:
    return results / f"live-run-{n}.json"


def load_run(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_run(path: Path, started: str, total: int, records: list[dict], complete: bool) -> None:
    """Atomic (temporary file + rename): an interruption never leaves a truncated file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"started": started, "complete": complete, "cases_total": total, "cases": records},
                              indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run(n: int, cases: list[dict], phase2, phase1, provider, results: Path = RESULTS, token_budget: int | None = None,
        chunks_by_doc: dict | None = None, log=print) -> int:
    """Run (or resume) live-run-<n>.json. Returns an exit code (EXIT_*)."""
    results.mkdir(parents=True, exist_ok=True)
    path = run_path(results, n)
    state = load_run(path) or {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "cases": []}
    done = {r["id"]: r for r in state["cases"] if r["done"]}
    records: list[dict | None] = [done.get(c["id"]) for c in cases]  # errored or missing cases: None (to run)
    saved = lambda complete: save_run(path, state["started"], len(cases), [r for r in records if r], complete)  # noqa: E731
    if done:
        log(f"resuming {path.name}: {len(done)} done case(s) kept, {records.count(None)} to run")
    used = 0
    for i, case in enumerate(cases):
        if records[i] is not None:
            continue
        if token_budget is not None and used >= token_budget:
            saved(False)
            log(f"stopped: token budget {token_budget} reached ({used} used); rerun the same command to resume")
            return EXIT_BUDGET
        rec = run_case(case, phase2, phase1, provider, chunks_by_doc)
        records[i] = rec
        used += rec["tokens"]
        saved(False)
        log(f"run {n} {case['id']:22} {rec['phase2']['strategy']:19} P2 {rec['phase2']['status']:9} "
            f"calls {rec['phase2']['llm_calls']}+{rec['phase1']['llm_calls']} tokens {rec['tokens']:6} "
            f"{'; '.join(rec['checks']) or 'ok'}")
        if not rec["done"]:
            log(f"stopped: provider error in {case['id']} (saved, will be redone on resume); {used} tokens used")
            return EXIT_PROVIDER_ERROR
    saved(True)
    log(f"run {n} complete; {used} tokens used in this invocation")
    return EXIT_OK


# ---------------------------------------------------------------------------------------------- summary


def write_summary(results: Path = RESULTS) -> str:
    runs = []
    n = 1
    while run_path(results, n).exists():
        runs.append((n, load_run(run_path(results, n))))
        n += 1
    out = ["# Phase 2 live evaluation", "",
           "Phase 2 = MultiIntentController; phase 1 = AdaptiveController, run separately only when phase 2 did not "
           "delegate (a delegated answer is phase 1's own). Checks are automatic (evaluation/multi_intent/live.py), "
           "not a verdict on correctness.", ""]
    for n, data in runs:
        recs = data["cases"]
        done = [r for r in recs if r["done"]]
        state = "complete" if data["complete"] else "INCOMPLETE"
        out += [f"## Run {n} ({state}): {sum(not r['checks'] for r in done)}/{len(done)} done cases pass the checks, "
                f"{len(done)}/{data['cases_total']} done", "",
                f"LLM calls: phase 2 {sum(r['phase2']['llm_calls'] for r in recs)}, phase 1 comparison "
                f"{sum(r['phase1']['llm_calls'] for r in recs)}; tokens {sum(r['tokens'] for r in recs)}; "
                f"provider errors: {sum(bool(r['provider_errors']) for r in recs)}", "",
                "| case | strategy | P2 status | intents | P2 calls | P1 (run: status, calls) | tokens | checks |",
                "|---|---|---|---|---|---|---|---|"]
        for r in recs:
            p2, p1 = r["phase2"], r["phase1"]
            intents = ", ".join(f"{i['index'] + 1}:{i['status']}" for i in p2["intents"]) if p2["decomposition"]["multi"] else "—"
            p1s = f"{'yes' if p1['run'] else 'shared'}: {p1['status']}, {p1['llm_calls']}"
            out.append(f"| {r['id']} | {p2['strategy']} | {p2['status']} | {intents} | {p2['llm_calls']} | {p1s} | "
                       f"{r['tokens']} | {'; '.join(r['checks']) or 'ok'} |")
        out.append("")
    complete = [data for _, data in runs if data["complete"]]
    if len(complete) >= 2:
        by_id = [{r["id"]: r for r in data["cases"]} for data in complete]
        same = [cid for cid in by_id[0] if all(cid in b and (b[cid]["phase2"]["strategy"], b[cid]["phase2"]["status"])
                                               == (by_id[0][cid]["phase2"]["strategy"], by_id[0][cid]["phase2"]["status"])
                                               for b in by_id[1:])]
        out += [f"## Agreement across {len(complete)} complete runs: same strategy and status in "
                f"{len(same)}/{len(by_id[0])} cases", ""]
    text = "\n".join(out)
    (results / "live-summary.md").write_text(text, encoding="utf-8")
    return text


# ---------------------------------------------------------------------------------------------- CLI


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=int, default=1, help="run number: writes/resumes results/live-run-<n>.json")
    parser.add_argument("--token-budget", type=int, default=None,
                        help="stop before starting another case once this invocation used this many tokens")
    parser.add_argument("--summarize", action="store_true", help="rewrite live-summary.md from saved runs; no LLM calls")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    if args.summarize:
        print(write_summary())
        return EXIT_OK
    from generation import env

    env.load_env()  # project .env (git-ignored); an exported GROQ_API_KEY takes precedence
    if not os.environ.get("GROQ_API_KEY"):
        print("BLOCKED: GROQ_API_KEY is not set (environment or .env); no request was sent.")
        return EXIT_NO_KEY
    from adaptive.controller import AdaptiveController
    from adaptive.multi.controller import MultiIntentController
    from chunking.pipeline import load_chunks
    from evaluation.adaptive.run import AllCallsProvider
    from evaluation.multi_intent.cases import CASES
    from generation.pipeline import load_stack
    from generation.providers import get_provider

    stack = load_stack(rerank=True)
    provider = AllCallsProvider(get_provider("groq"))
    code = run(args.run, CASES, MultiIntentController(stack, provider), AdaptiveController(stack, provider), provider,
               token_budget=args.token_budget, chunks_by_doc=load_chunks())
    if code == EXIT_OK:
        print(write_summary())
    return code


if __name__ == "__main__":
    raise SystemExit(main())
