"""Phase 3 offline evaluation: follow-up resolution, temporal refinement, evidence and phase 2 equivalence, on the live
corpus with the real retrieval stack and NO language model.

  python -m evaluation.session.run            # writes results/offline.json and results/offline.md

Each conversation (evaluation/session/cases.py) runs turn by turn through one SessionController with a recording stub
model that answers "insufficient_evidence" (no network, no tokens). Recorded per turn: the resolution (kind, reason,
the query phase 2 receives, the temporal constraint in effect, organizations), phase 2's strategy, temporal intent and
selected versions, the model calls, the chunks shown to the model and their organizations, whether each gold unit
was shown, and, for a self-contained turn, whether its model input is byte-identical to phase 2 on the question alone.
The model's answers are not evaluated here (that needs a live run).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def gold_chunks(r: dict, cbd: dict) -> set[str]:
    """Every chunk of the referenced document that contains the quote (identical text may occur in several)."""
    from evaluation.integration.cases import DOCS

    hits = {c.chunk_id for c in cbd[DOCS[r["doc"]]] if _norm(r["quote"]) in _norm(c.text)}
    if not hits:
        raise ValueError(f"gold quote not found in {r['doc']}: {r['quote']!r}")
    return hits


def run_conversation(conv: dict, stack, cbd) -> dict:
    from adaptive.multi.controller import MultiIntentController
    from adaptive.session.controller import SessionController
    from adaptive.session.state import SELF_CONTAINED, Session
    from evaluation.integration.cases import DOCS
    from evaluation.multi_intent.run import RecordingStub

    stub = RecordingStub()
    ctl, session = SessionController(stack, stub), Session()
    by_id = {c.chunk_id: c for c in stack.chunks}
    turns = []
    for spec in conv["turns"]:
        before = len(stub.calls)
        t = ctl.ask(session, spec["question"])
        r, a = t.resolution, t.answer
        shown = list(dict.fromkeys(s["chunk_id"] for p in (a.phase1 if a else []) for s in p.sources_considered))
        selected = {}
        for i in a.intents if a else []:
            for series, docs in i["selected_versions"].items():
                selected.setdefault(series, sorted(set(selected.get(series, [])) | set(docs)))
        rec = {"question": spec["question"], "kind": r.kind, "reason": r.reason, "query": r.query, "topic": r.topic,
               "temporal_in_effect": r.temporal, "organizations": r.organizations, "anchor": r.anchor,
               "signals": r.signals, "strategy": a.strategy if a else None,
               "temporal_intent": a.intents[0]["temporal_intent"] if a and a.intents else None,
               "selected_versions": selected, "model_calls": len(stub.calls) - before, "context": shown,
               "context_organizations": sorted({by_id[c].organization for c in shown}),
               "gold_shown": [bool(set().union(*(gold_chunks(x, cbd) for x in unit)) & set(shown))
                              for unit in spec["gold"]],
               "identical_to_phase2": None, "checks": []}
        solo = RecordingStub()  # phase 2 on the question alone, without the session
        alone = MultiIntentController(stack, solo).run(spec["question"])
        if r.kind == SELF_CONTAINED:
            rec["identical_to_phase2"] = solo.calls == stub.calls[before:]
        else:
            ctx = {s["chunk_id"] for p in alone.phase1 for s in p.sources_considered}
            rec["without_session"] = {
                "gold_shown": [bool(set().union(*(gold_chunks(x, cbd) for x in unit)) & ctx) for unit in spec["gold"]],
                "context_organizations": sorted({by_id[c].organization for c in ctx}), "model_calls": len(solo.calls)}
        rec["checks"] = checks(spec, rec, DOCS)
        turns.append(rec)
    return {"id": conv["id"], "tags": conv["tags"], "turns": turns}


def checks(spec: dict, rec: dict, docs: dict) -> list[str]:
    out = []
    if rec["kind"] != spec["kind"]:
        out.append(f"resolution {rec['kind']}, expected {spec['kind']}")
    if rec["kind"] == "unresolved" and rec["model_calls"]:
        out.append("unresolved but the model was called")
    if rec["identical_to_phase2"] is False:
        out.append("self-contained but the model input differs from phase 2")
    if spec["temporal"] and rec["temporal_intent"] != spec["temporal"]:
        out.append(f"temporal intent {rec['temporal_intent']}, expected {spec['temporal']}")
    if spec["version"] and not any(docs[spec["version"]] in d for d in rec["selected_versions"].values()):
        out.append(f"version {spec['version']} not selected ({rec['selected_versions']})")
    if not all(rec["gold_shown"]):
        out.append(f"gold evidence not shown to the model ({rec['gold_shown']})")
    return out


def write_md(convs: list[dict]) -> str:
    turns = [t for c in convs for t in c["turns"]]
    ok = sum(not t["checks"] for t in turns)
    out = ["# Phase 3 offline evaluation (no language model)", "",
           "Real retrieval stack (hybrid → temporal → rerank), stub model; see evaluation/session/run.py. "
           "\"gold\" = each gold unit shown to the model, with the session → for the question alone (phase 2 without "
           "the session); \"= P2\" = a self-contained turn's model input is byte-identical to phase 2 on the question "
           "alone.", "",
           f"## {ok}/{len(turns)} turns pass the offline checks ({len(convs)} conversations)", "",
           "| conversation | turn | question | resolution | query phase 2 receives | temporal / versions | calls | gold | = P2 | checks |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    marks = lambda xs: "".join("✓" if g else "✗" for g in xs) or "–"  # noqa: E731
    for c in convs:
        for k, t in enumerate(c["turns"], 1):
            versions = "; ".join(d for docs in t["selected_versions"].values() for d in docs) or "–"
            gold = marks(t["gold_shown"])
            if "without_session" in t and t["gold_shown"]:
                gold += " → alone " + marks(t["without_session"]["gold_shown"])
            same = "–" if t["identical_to_phase2"] is None else str(t["identical_to_phase2"])
            query = "(verbatim)" if t["kind"] == "self_contained" else (t["query"] or "— (no retrieval)")
            out.append(f"| {c['id']} | {k} | {t['question']} | {t['kind']}: {t['reason']} | {query} | "
                       f"{t['temporal_intent'] or '–'} / {versions} | {t['model_calls']} | {gold} | {same} | "
                       f"{'; '.join(t['checks']) or 'ok'} |")
    follow = [t for t in turns if t.get("without_session") and t["gold_shown"]]
    alone = sum(all(t["without_session"]["gold_shown"]) for t in follow)
    out += ["", f"Follow-ups with gold evidence: {sum(all(t['gold_shown']) for t in follow)}/{len(follow)} show it with "
                f"the session, {alone}/{len(follow)} when phase 2 gets the follow-up alone."]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    import os

    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from chunking.pipeline import load_chunks
    from evaluation.session.cases import CONVERSATIONS
    from generation.pipeline import load_stack

    stack, cbd = load_stack(rerank=True), load_chunks()
    convs = []
    for conv in CONVERSATIONS:
        rec = run_conversation(conv, stack, cbd)
        convs.append(rec)
        for k, t in enumerate(rec["turns"], 1):
            print(f"{conv['id']:14} {k} {t['kind']:14} calls={t['model_calls']} {'; '.join(t['checks']) or 'ok'}",
                  flush=True)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "offline.json").write_text(json.dumps({"conversations": convs}, indent=1, ensure_ascii=False) + "\n",
                                          encoding="utf-8")
    md = write_md(convs)
    (RESULTS / "offline.md").write_text(md, encoding="utf-8")
    print(md)
    return 1 if any(t["checks"] for c in convs for t in c["turns"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
