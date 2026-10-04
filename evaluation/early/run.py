"""Early-retrieval evaluation: does retrieval start before the utterance is complete? Real retrieval stack, NO
language model, a deterministic transcript clock.

  python -m evaluation.early.run            # writes results/offline.json and results/offline.md

Each utterance is fed to the existing controller one word at a time. A word arrives every WORD_MS milliseconds
(150 words per minute), so word k arrives at k * WORD_MS and the utterance is complete at len(words) * WORD_MS.
After each word but the last, the transcript so far is given to the controller's retrieval-intent prediction
(adaptive.session.gate.predicts_retrieval). The first time it predicts retrieval, the partial transcript is resolved
against the session exactly as a complete question would be (adaptive.session.resolve) and retrieved with the
controller's own retriever: that is the retrieval TRIGGER, at that word's timestamp. When the last word has arrived,
the complete utterance goes through SessionController.ask, unchanged: its decision (wait / suppress / presentation /
retrieve / refine) and whether it required retrieval are the controller's, not this harness's.

Recorded per utterance: the word timestamps, the trigger (word, timestamp, partial transcript, query), the completion
timestamp, the lead (completion - trigger), the measured wall time of the early retrieval, the final decision, and
how much of the context the complete utterance is answered from was already in the early retrieval's context.

  eligible        the controller required retrieval for the complete utterance
  early           eligible, and the trigger came before the completion timestamp
  early and done  early, and the early retrieval had also finished before the completion timestamp (trigger
                  timestamp + its measured wall time, on this machine)
  false trigger   a trigger for an utterance that, complete, required no retrieval

The early retrieval is a pre-fetch: SessionController.ask still retrieves for the complete utterance, and the
answer is grounded in that retrieval only. "context overlap" says how useful the pre-fetch would have been.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
WORD_MS = 400  # one word every 400 ms = 150 words per minute


def simulate(controller, session, utterance: str, clock=time.perf_counter) -> dict:
    """One utterance, word by word, through `controller` on `session` (see the module docstring)."""
    from adaptive.session.gate import predicts_retrieval
    from adaptive.session.resolve import resolve
    from adaptive.session.state import UNRESOLVED
    from generation.context import assemble

    multi = controller.multi
    words = utterance.split()
    completion_ms = len(words) * WORD_MS
    trigger = None
    for k in range(1, len(words)):  # the transcript after each word but the last
        partial = " ".join(words[:k])
        if not predicts_retrieval(partial):
            continue
        r = resolve(partial, session.context, multi.organizations, multi.aliases)
        if r.kind == UNRESOLVED:  # nothing can be retrieved for it yet
            continue
        query = r.signals.get("retrieval_query", r.query)
        start = clock()
        retrieval = multi.aligned(multi.stack, query)
        seconds = clock() - start
        trigger = {"word": k, "ms": k * WORD_MS, "partial": partial, "resolution": r.kind, "query": query,
                   "retrieval_ms": round(seconds * 1000, 1),
                   "context": [s.chunk.chunk_id for s in assemble(retrieval.evidence).sources]}
        break
    turn = controller.ask(session, utterance)  # the complete utterance: the existing controller, unchanged
    answer = turn.answer
    final_context = list(dict.fromkeys(s["chunk_id"] for p in (answer.phase1 if answer else []) for s in p.sources_considered))
    required = bool(turn.resolution.signals.get("retrieval_required"))
    rec = {"utterance": utterance, "words": len(words), "word_ms": WORD_MS, "completion_ms": completion_ms,
           "trigger": trigger, "decision": turn.resolution.signals.get("decision"), "resolution": turn.resolution.kind,
           "retrieval_required": required, "final_context": final_context}
    rec["early"] = required and trigger is not None
    rec["false_trigger"] = (not required) and trigger is not None
    if trigger is not None:
        rec["lead_ms"] = completion_ms - trigger["ms"]
        rec["early_and_done"] = required and trigger["ms"] + trigger["retrieval_ms"] <= completion_ms
        if required and final_context:
            rec["context_overlap"] = round(len(set(trigger["context"]) & set(final_context)) / len(final_context), 3)
    return rec


def utterances() -> list[dict]:
    """Every utterance of the evaluation, with the suite it comes from; a conversation is a list of turns."""
    from evaluation.integration.cases import CASES as INTEGRATION
    from evaluation.multi_intent.cases import CASES as MULTI
    from evaluation.session.cases import CONVERSATIONS
    from evaluation.streaming.cases import CASES as STREAMING

    from .cases import ACKNOWLEDGEMENTS, CONTEXT, LAYOUT_REQUESTS, UNFINISHED

    out = [{"suite": "integration", "id": c["id"], "turns": [c["question"]]} for c in INTEGRATION]
    out += [{"suite": "multi_intent", "id": c["id"], "turns": [c["question"]]} for c in MULTI]
    out += [{"suite": "streaming", "id": c["id"], "turns": [c["question"]]} for c in STREAMING]
    out += [{"suite": "session", "id": c["id"], "turns": [t["question"] for t in c["turns"]]} for c in CONVERSATIONS]
    out += [{"suite": "acknowledgement", "id": f"ack-{i}", "turns": [CONTEXT, u], "measure": [1]}
            for i, u in enumerate(ACKNOWLEDGEMENTS, 1)]
    out += [{"suite": "layout_request", "id": f"layout-{i}", "turns": [CONTEXT, u], "measure": [1]}
            for i, u in enumerate(LAYOUT_REQUESTS, 1)]
    out += [{"suite": "unfinished", "id": f"unfinished-{i}", "turns": [u]} for i, u in enumerate(UNFINISHED, 1)]
    return out


def summarize(records: list[dict]) -> dict:
    eligible = [r for r in records if r["retrieval_required"]]
    others = [r for r in records if not r["retrieval_required"]]
    early = [r for r in eligible if r["early"]]
    done = [r for r in early if r["early_and_done"]]
    overlaps = [r["context_overlap"] for r in early if "context_overlap" in r]

    def rate(n: int, d: int) -> float | None:
        return round(n / d, 4) if d else None

    by_suite = {}
    for r in records:
        s = by_suite.setdefault(r["suite"], {"utterances": 0, "eligible": 0, "early": 0, "false_triggers": 0})
        s["utterances"] += 1
        s["eligible"] += r["retrieval_required"]
        s["early"] += r["early"]
        s["false_triggers"] += r["false_trigger"]
    return {"utterances": len(records), "eligible": len(eligible), "early": len(early),
            "early_rate": rate(len(early), len(eligible)), "early_and_done": len(done),
            "early_and_done_rate": rate(len(done), len(eligible)),
            "no_retrieval_utterances": len(others), "false_triggers": sum(r["false_trigger"] for r in others),
            "false_trigger_rate": rate(sum(r["false_trigger"] for r in others), len(others)),
            "decisions_of_no_retrieval_utterances": {d: sum(1 for r in others if r["decision"] == d)
                                                     for d in sorted({str(r["decision"]) for r in others})},
            "mean_lead_ms": round(sum(r["lead_ms"] for r in early) / len(early), 1) if early else None,
            "mean_early_retrieval_ms": round(sum(r["trigger"]["retrieval_ms"] for r in early) / len(early), 1) if early else None,
            "early_context_overlap_mean": round(sum(overlaps) / len(overlaps), 3) if overlaps else None,
            "early_context_with_any_overlap": rate(sum(1 for o in overlaps if o > 0), len(overlaps)),
            "by_suite": by_suite}


def report(records: list[dict], summary: dict) -> str:
    s = summary
    pct = lambda x: "n/a" if x is None else f"{100 * x:.1f}%"  # noqa: E731
    out = ["# Early-retrieval evaluation (no language model)", "",
           f"Real retrieval stack, stub model, a word every {WORD_MS} ms; see evaluation/early/run.py for the method "
           "and the definitions. The eligible utterances are the questions of the existing suites; whether an utterance "
           "is eligible is the controller's own decision on the complete utterance.", "",
           "## Summary", "",
           f"* Utterances: {s['utterances']}; eligible (retrieval required when complete): **{s['eligible']}**.",
           f"* Retrieval triggered before the utterance was complete: **{s['early']}/{s['eligible']} = {pct(s['early_rate'])}**.",
           f"* Triggered and the early retrieval also finished before completion (measured wall time on this machine): "
           f"{s['early_and_done']}/{s['eligible']} = {pct(s['early_and_done_rate'])}.",
           f"* Utterances that required no retrieval: {s['no_retrieval_utterances']} "
           f"({', '.join(f'{k}: {v}' for k, v in s['decisions_of_no_retrieval_utterances'].items())}); "
           f"false triggers: **{s['false_triggers']}/{s['no_retrieval_utterances']} = {pct(s['false_trigger_rate'])}**.",
           f"* Mean lead of the trigger over completion: {s['mean_lead_ms']} ms; mean early retrieval wall time: "
           f"{s['mean_early_retrieval_ms']} ms.",
           f"* Early context vs the context the complete utterance was answered from: mean overlap "
           f"{s['early_context_overlap_mean']}; some overlap in {pct(s['early_context_with_any_overlap'])} of the early retrievals.",
           "", "| suite | utterances | eligible | early | false triggers |", "|---|---|---|---|---|"]
    out += [f"| {name} | {v['utterances']} | {v['eligible']} | {v['early']} | {v['false_triggers']} |"
            for name, v in s["by_suite"].items()]
    out += ["", "## Utterances", "",
            "| suite | case | utterance | words | completion ms | decision | required | trigger word | trigger ms | lead ms | "
            "retrieval ms | overlap | partial transcript at the trigger |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in records:
        t = r["trigger"] or {}
        text = r["utterance"] if len(r["utterance"]) <= 70 else r["utterance"][:67] + "..."
        out.append(f"| {r['suite']} | {r['id']} | {text} | {r['words']} | {r['completion_ms']} | {r['decision']} | "
                   f"{r['retrieval_required']} | {t.get('word', '–')} | {t.get('ms', '–')} | {r.get('lead_ms', '–')} | "
                   f"{t.get('retrieval_ms', '–')} | {r.get('context_overlap', '–')} | {t.get('partial', '–')} |")
    return "\n".join(out) + "\n"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    from adaptive.session.controller import SessionController
    from adaptive.session.state import Session
    from evaluation.multi_intent.run import RecordingStub
    from generation.pipeline import load_stack

    stack = load_stack(rerank=True)
    controller = SessionController(stack, RecordingStub())
    controller.multi.aligned(stack, "warm up the retrieval stack")  # the first call loads lazily: not a measurement
    records = []
    for case in utterances():
        session = Session()
        for n, utterance in enumerate(case["turns"]):
            rec = simulate(controller, session, utterance)
            if n in case.get("measure", range(len(case["turns"]))):
                records.append({"suite": case["suite"], "id": case["id"], "turn": n, **rec})
    summary = summarize(records)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "offline.json").write_text(json.dumps({"word_ms": WORD_MS, "summary": summary, "utterances": records},
                                                     indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    text = report(records, summary)
    (RESULTS / "offline.md").write_text(text, encoding="utf-8")
    print(text.split("## Utterances")[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
