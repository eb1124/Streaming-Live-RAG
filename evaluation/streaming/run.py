"""Phase 6 offline evaluation: the frozen retrieval (baseline) vs bounded iterative retrieval (streaming), on the live
corpus with the real retrieval stack and NO language model.

  python -m evaluation.streaming.run            # writes results/offline.json and results/offline.md

Three suites, each run twice with its own recording stub model ("insufficient_evidence"; no network, no tokens):
  integration  the 27 frozen integration questions: AdaptiveController.run (baseline) vs StreamingController.run
  phase2       the 16 phase 2 cases: MultiIntentController without / with the iterative retriever
  phase3       the 8 phase 3 conversations: SessionController without / with the iterative retriever
Recorded per question (turn): retrieval rounds, queries, strategies, stop reason, coverage per round, evidence counts
(retrieved per round, new, retained, promoted), the chunks shown to the model, gold evidence shown (baseline ->
streaming), forbidden evidence shown (integration), the relevance gate's outcome, and whether the model input is
byte-identical to the baseline's.

Answer, citation and abstention correctness need the model, so they are not measured here. What is measured: where
the model input is identical to the baseline's, the answer is the baseline's (the committed live integration runs,
evaluation/integration/results/run-*.json, say which baseline answers were correct); where it differs, the case is
listed with the reason, the evidence it gained or lost, and needs a live run before its answer is known.
Checks: gold evidence the baseline showed the model must still be shown; no forbidden chunk may be newly shown; a
question the relevance gate stopped must still be stopped; phase 3 resolutions must not change.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
INTEGRATION_RUNS = HERE.parent / "integration" / "results"


def shown(answers) -> list[str]:
    return list(dict.fromkeys(s["chunk_id"] for a in answers for s in a.sources_considered))


def units_shown(units, ctx) -> list[bool]:
    return [bool(u & set(ctx)) for u in units]


def summarize(traces) -> dict:
    """The loop(s) behind one question: one trace per retrieval (phase 2 makes one per intent as well)."""
    return {"retrievals": len(traces), "rounds": [t.iterations for t in traces],
            "stop_reasons": [t.stop_reason for t in traces],
            "queries": [q for t in traces for q in t.queries],
            "strategies": [r.strategy + (f" ({r.target})" if r.target else "") for t in traces for r in t.rounds],
            "coverage": [f"{r.coverage.decision}: {r.coverage.reason}" for t in traces for r in t.rounds],
            "retrieved": sum(len(r.retrieved) for t in traces for r in t.rounds),
            "new": sum(len(r.new) for t in traces for r in t.rounds),
            "retained": [len(t.final.retained) for t in traces],
            "promoted": [c for t in traces for r in t.rounds for c in r.promoted],
            "skipped": [s for t in traces for s in t.skipped]}


def gold_units(spec_gold, cbd) -> list[set[str]]:
    from evaluation.session.run import gold_chunks

    return [set().union(*(gold_chunks(x, cbd) for x in unit)) for unit in spec_gold]


def baseline_correct() -> dict[str, bool]:
    """Integration case -> passed every automatic check in every committed live run (the frozen baseline)."""
    out: dict[str, list[bool]] = {}
    for path in sorted(INTEGRATION_RUNS.glob("run-*.json")):
        for c in json.loads(path.read_text(encoding="utf-8"))["cases"]:
            out.setdefault(c["id"], []).append(bool(c["checks"].get("passed")))
    return {k: all(v) for k, v in out.items()}


def integration(stack, cbd, correct, cases=None) -> list[dict]:
    from adaptive.controller import AdaptiveController
    from adaptive.streaming.controller import StreamingController
    from evaluation.integration.cases import CASES
    from evaluation.multi_intent.run import RecordingStub

    out = []
    for case in CASES if cases is None else cases:
        q = case["question"]
        sb, ss, traces = RecordingStub(), RecordingStub(), []
        b = AdaptiveController(stack, sb).run(q)
        s = StreamingController(stack, ss, observer=traces.append).run(q)
        cb, cs = shown([b]), shown([s.answer])
        units = gold_units(case["gold"], cbd)
        forbidden = set().union(*gold_units([[f] for f in case["forbidden"]], cbd)) if case.get("forbidden") else set()
        orgs = {c.chunk_id: c.organization for c in stack.chunks}
        expected_orgs = case["orgs"] or []  # None: the question's organization is not in the corpus
        rec = {"id": case["id"], "question": q, "expect": case["expect"], "baseline_correct": correct.get(case["id"]),
               "loop": summarize(traces), "identical_model_input": sb.calls == ss.calls,
               "baseline": {"abstention_reason": b.abstention_reason, "calls": len(sb.calls), "context": cb},
               "streaming": {"abstention_reason": s.answer.abstention_reason, "calls": len(ss.calls), "context": cs},
               "gold_shown": [units_shown(units, cb), units_shown(units, cs)],
               "forbidden_shown": [sorted(forbidden & set(cb)), sorted(forbidden & set(cs))],
               "other_org_in_context": [sum(orgs[c] not in expected_orgs for c in cb),
                                        sum(orgs[c] not in expected_orgs for c in cs)],
               "gained": [c for c in cs if c not in cb], "lost": [c for c in cb if c not in cs],
               "context_organizations": [dict(Counter(orgs[c] for c in cb)), dict(Counter(orgs[c] for c in cs))]}
        rec["checks"] = checks_integration(rec)
        out.append(rec)
    return out


def checks_integration(rec: dict) -> list[str]:
    out = []
    before, after = rec["gold_shown"]
    if any(x and not y for x, y in zip(before, after)):
        out.append("gold evidence the baseline showed is no longer shown")
    if set(rec["forbidden_shown"][1]) - set(rec["forbidden_shown"][0]):
        out.append("a forbidden chunk is newly shown to the model")
    if rec["baseline"]["abstention_reason"] == "low_relevance" and rec["streaming"]["abstention_reason"] != "low_relevance":
        out.append("the relevance gate stopped the baseline but not the loop")
    return out


def phase2(stack, cbd) -> list[dict]:
    from adaptive.multi.controller import MultiIntentController
    from adaptive.streaming.controller import IterativeRetriever
    from evaluation.multi_intent.cases import CASES
    from evaluation.multi_intent.run import RecordingStub

    out = []
    for case in CASES:
        sb, ss, traces = RecordingStub(), RecordingStub(), []
        b = MultiIntentController(stack, sb).run(case["question"])
        s = MultiIntentController(stack, ss, retriever=IterativeRetriever(observer=traces.append)).run(case["question"])
        cb, cs = shown(b.phase1), shown(s.phase1)
        rec = {"id": case["id"], "question": case["question"], "loop": summarize(traces),
               "strategy": [b.strategy, s.strategy], "calls": [len(sb.calls), len(ss.calls)],
               "identical_model_input": sb.calls == ss.calls, "intents": []}
        for part in case["intents"]:
            units = gold_units(part["gold"], cbd) if part["expect"] == "answer" else []
            rec["intents"].append({"expect": part["expect"], "gold_shown": [units_shown(units, cb), units_shown(units, cs)]})
        rec["checks"] = [f"intent {k + 1}: gold evidence the baseline showed is no longer shown"
                         for k, i in enumerate(rec["intents"])
                         if any(x and not y for x, y in zip(*i["gold_shown"]))]
        out.append(rec)
    return out


def phase3(stack, cbd) -> list[dict]:
    from adaptive.session.controller import SessionController
    from adaptive.session.state import Session
    from adaptive.streaming.controller import IterativeRetriever
    from evaluation.multi_intent.run import RecordingStub
    from evaluation.session.cases import CONVERSATIONS

    out = []
    for conv in CONVERSATIONS:
        sb, ss, traces = RecordingStub(), RecordingStub(), []
        base = SessionController(stack, sb)
        loop = SessionController(stack, ss, retriever=IterativeRetriever(observer=traces.append))
        s_b, s_s = Session(), Session()
        turns = []
        for spec in conv["turns"]:
            before_b, before_s, before_t = len(sb.calls), len(ss.calls), len(traces)
            tb, ts = base.ask(s_b, spec["question"]), loop.ask(s_s, spec["question"])
            cb, cs = shown(tb.answer.phase1 if tb.answer else []), shown(ts.answer.phase1 if ts.answer else [])
            units = gold_units(spec["gold"], cbd)
            rec = {"question": spec["question"], "kind": [tb.resolution.kind, ts.resolution.kind],
                   "same_resolution": tb.resolution == ts.resolution, "loop": summarize(traces[before_t:]),
                   "identical_model_input": sb.calls[before_b:] == ss.calls[before_s:],
                   "gold_shown": [units_shown(units, cb), units_shown(units, cs)]}
            rec["checks"] = (["resolution changed"] if not rec["same_resolution"] else []) + (
                ["gold evidence the baseline showed is no longer shown"]
                if any(x and not y for x, y in zip(*rec["gold_shown"])) else [])
            turns.append(rec)
        out.append({"id": conv["id"], "turns": turns})
    return out


def _marks(xs) -> str:
    return "".join("✓" if x else "✗" for x in xs) or "–"


def write_md(integ, p2, p3, p6) -> str:
    rounds = [r for rec in integ for r in rec["loop"]["rounds"]]
    one, many = sum(r == 1 for r in rounds), sum(r > 1 for r in rounds)
    unresolved = [rec["id"] for rec in integ if rec["loop"]["stop_reasons"][0] in ("max_rounds", "no_new_refinement")]
    structural = [rec["id"] for rec in integ if rec["loop"]["stop_reasons"][0] == "structurally_unresolved"]
    same = sum(rec["identical_model_input"] for rec in integ)
    correct_same = sum(rec["identical_model_input"] for rec in integ if rec["baseline_correct"])
    n_correct = sum(bool(rec["baseline_correct"]) for rec in integ)
    fails = sum(bool(r["checks"]) for r in integ + p6) + sum(bool(r["checks"]) for r in p2) + sum(
        bool(t["checks"]) for c in p3 for t in c["turns"])
    out = ["# Phase 6 offline evaluation (no language model)", "",
           "Baseline = the frozen retrieval (generation.pipeline.retrieve); streaming = bounded iterative retrieval "
           "(adaptive/streaming, at most 3 rounds). Real retrieval stack, stub model; see evaluation/streaming/run.py. "
           "Answers are not evaluated offline: an identical model input means the baseline's answer; a changed input "
           "needs a live run.", "",
           f"## Summary: {fails} check failures", "",
           f"* Integration questions: {one}/{len(integ)} stop after one round, {many} take more; still insufficient "
           f"after the budget: {', '.join(unresolved) or 'none'}; structurally unresolved: {', '.join(structural) or 'none'}.",
           f"* Model input identical to the baseline for {same}/{len(integ)} integration questions, including "
           f"{correct_same}/{n_correct} whose baseline answer passed every check in every committed live run.",
           f"* Phase 2 cases: {sum(r['identical_model_input'] for r in p2)}/{len(p2)} with identical model input; "
           f"strategy changed for {sum(r['strategy'][0] != r['strategy'][1] for r in p2)}.",
           f"* Phase 3 turns: {sum(t['same_resolution'] for c in p3 for t in c['turns'])}/"
           f"{sum(len(c['turns']) for c in p3)} with the same resolution, "
           f"{sum(t['identical_model_input'] for c in p3 for t in c['turns'])} with identical model input.", "",
           "## Integration questions", "",
           "| case | expect | baseline correct | rounds | stop | queries after the first | gold B→S | forbidden B→S | other-org sources B→S | gate B→S | = input | checks |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in integ:
        lp = r["loop"]
        out.append(f"| {r['id']} | {r['expect']} | {r['baseline_correct']} | {lp['rounds'][0]} | {lp['stop_reasons'][0]} | "
                   f"{'<br>'.join(lp['queries'][1:]) or '–'} | {_marks(r['gold_shown'][0])}→{_marks(r['gold_shown'][1])} | "
                   f"{len(r['forbidden_shown'][0])}→{len(r['forbidden_shown'][1])} | "
                   f"{r['other_org_in_context'][0]}→{r['other_org_in_context'][1]} | "
                   f"{r['baseline']['abstention_reason']}→{r['streaming']['abstention_reason']} | "
                   f"{r['identical_model_input']} | {'; '.join(r['checks']) or 'ok'} |")
    changed = [r for r in integ if not r["identical_model_input"]]
    out += ["", "### Integration questions whose model input changed", ""]
    for r in changed:
        lp = r["loop"]
        out.append(f"* **{r['id']}** (expect {r['expect']}): {'; '.join(lp['coverage'])}. Stopped: {lp['stop_reasons'][0]}. "
                   f"Gained {len(r['gained'])} context chunk(s), lost {len(r['lost'])}"
                   + (f"; promoted {', '.join(lp['promoted'])}" if lp["promoted"] else "") + ".")
    if not changed:
        out.append("None.")
    out += ["", "## Phase 6 cases (evaluation/streaming/cases.py: from the phase 2 manual review; not a blind test)", "",
            "| case | rounds | stop | queries after the first | promoted | gold B→S | context organizations B→S | = input | checks |",
            "|---|---|---|---|---|---|---|---|---|"]
    for r in p6:
        lp = r["loop"]
        out.append(f"| {r['id']} | {lp['rounds'][0]} | {lp['stop_reasons'][0]} | {'<br>'.join(lp['queries'][1:]) or '–'} | "
                   f"{', '.join(lp['promoted']) or '–'} | {_marks(r['gold_shown'][0])}→{_marks(r['gold_shown'][1])} | "
                   f"{r['context_organizations'][0]} → {r['context_organizations'][1]} | {r['identical_model_input']} | "
                   f"{'; '.join(r['checks']) or 'ok'} |")
    out += ["", "## Phase 2 cases", "",
            "| case | retrievals | rounds per retrieval | strategy B→S | calls B→S | gold per intent B→S | = input | checks |",
            "|---|---|---|---|---|---|---|---|"]
    for r in p2:
        gold = "; ".join(f"{_marks(i['gold_shown'][0])}→{_marks(i['gold_shown'][1])}" for i in r["intents"]) or "–"
        out.append(f"| {r['id']} | {r['loop']['retrievals']} | {r['loop']['rounds']} | {r['strategy'][0]}→{r['strategy'][1]} | "
                   f"{r['calls'][0]}→{r['calls'][1]} | {gold} | {r['identical_model_input']} | {'; '.join(r['checks']) or 'ok'} |")
    out += ["", "## Phase 3 conversations", "",
            "| conversation | turn | resolution B / S | rounds per retrieval | gold B→S | = input | checks |",
            "|---|---|---|---|---|---|---|"]
    for c in p3:
        for k, t in enumerate(c["turns"], 1):
            out.append(f"| {c['id']} | {k} | {t['kind'][0]} / {t['kind'][1]} | {t['loop']['rounds'] or '–'} | "
                       f"{_marks(t['gold_shown'][0])}→{_marks(t['gold_shown'][1])} | {t['identical_model_input']} | "
                       f"{'; '.join(t['checks']) or 'ok'} |")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    import os

    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from chunking.pipeline import load_chunks
    from generation.pipeline import load_stack

    stack, cbd = load_stack(rerank=True), load_chunks()
    integ = integration(stack, cbd, baseline_correct())
    print("integration done", flush=True)
    p2 = phase2(stack, cbd)
    print("phase 2 done", flush=True)
    p3 = phase3(stack, cbd)
    print("phase 3 done", flush=True)
    from evaluation.streaming.cases import CASES as PHASE6

    p6 = integration(stack, cbd, {}, PHASE6)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "offline.json").write_text(json.dumps({"integration": integ, "phase6": p6, "phase2": p2, "phase3": p3},
                                                     indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    md = write_md(integ, p2, p3, p6)
    (RESULTS / "offline.md").write_text(md, encoding="utf-8")
    print(md)
    failed = any(r["checks"] for r in integ + p6) or any(r["checks"] for r in p2) or any(
        t["checks"] for c in p3 for t in c["turns"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
