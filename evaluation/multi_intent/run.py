"""Phase 2 offline evaluation: decomposition, coverage gate, per-intent evidence and phase 1 equivalence, on the live
corpus with the real retrieval stack and NO language model.

  python -m evaluation.multi_intent.run            # writes results/offline.json and results/offline.md

Every question runs through phase 1 (AdaptiveController.run) and phase 2 (MultiIntentController.run), each with its
own recording stub model that answers "insufficient_evidence" (no network, no tokens). Recorded per question:
  * the decomposition, the strategy and the reason; per intent its sub-query, temporal intent, selected versions,
    best cross-encoder score, admissibility, best chunk and whether the question's own context covers it
  * the model input: phase 2's calls must be byte-identical to phase 1's whenever phase 2 delegates
  * for the phase 2 cases: for each intent's gold evidence, whether it is in the context phase 1 shows the model and
    in the context(s) phase 2 shows the model (the evidence fusion is meant to recover)
Questions: the phase 2 cases (evaluation/multi_intent/cases.py) and, read only, the 27 integration questions.
The model's answers are not evaluated here (that needs a live run).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


class RecordingStub:
    name, model = "offline-stub", "none"

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user, schema):
        from generation.providers import ProviderResponse

        self.calls.append((system, user))
        text = json.dumps({"status": "insufficient_evidence", "claims": [], "not_in_sources": [],
                           "abstention_reason": "offline evaluation: no model"})
        return ProviderResponse(text=text, provider=self.name, model=self.model, finish_reason="stop")


def shown(answers) -> list[str]:
    return list(dict.fromkeys(s["chunk_id"] for a in answers for s in a.sources_considered))


def evaluate(question: str, stack, cbd, case: dict | None = None) -> dict:
    from adaptive.controller import AdaptiveController
    from adaptive.multi.controller import MultiIntentController
    from evaluation.integration.cases import resolve

    s1, s2 = RecordingStub(), RecordingStub()
    a1 = AdaptiveController(stack, s1).run(question)
    a2 = MultiIntentController(stack, s2).run(question)
    p1_ctx, p2_ctx = shown([a1]), shown(a2.phase1)
    rec = {"question": question, "multi": a2.decomposition["multi"], "decomposition_reason": a2.decomposition["reason"],
           "flags": a2.decomposition["flags"], "strategy": a2.strategy, "reason": a2.reason,
           "phase1_calls": len(s1.calls), "phase2_calls": len(s2.calls), "identical_model_input": s1.calls == s2.calls,
           "phase1_context": p1_ctx, "phase2_context": p2_ctx,
           "intents": [{k: i[k] for k in ("index", "text", "sub_query", "carried", "outside_corpus", "temporal_intent",
                                         "selected_versions",
                                         "best_rerank", "admissible", "top_chunk", "covered_by_question_context")}
                       for i in a2.intents]}
    if case is not None:
        rec |= {"id": case["id"], "tags": case["tags"], "expected_multi": case["multi"],
                "expected_strategy": case["strategy"]}
        if case["multi"] and len(case["intents"]) == len(a2.intents):
            for i, part in zip(rec["intents"], case["intents"]):
                units = [{resolve(r, cbd) for r in u} for u in part["gold"]]
                i["expect"] = part["expect"]
                i["gold_in_phase1_context"] = [bool(u & set(p1_ctx)) for u in units]
                i["gold_in_phase2_context"] = [bool(u & set(p2_ctx)) for u in units]
    return rec


def checks(rec: dict) -> list[str]:
    """Automatic offline checks (phase 2 cases): decomposition, determined strategy, equivalence, gold coverage."""
    out = []
    if rec["multi"] != rec["expected_multi"]:
        out.append(f"decomposition: multi={rec['multi']} expected {rec['expected_multi']}")
    if rec["expected_strategy"] and rec["strategy"] != rec["expected_strategy"]:
        out.append(f"strategy: {rec['strategy']} expected {rec['expected_strategy']}")
    if rec["strategy"] == "delegate" and not rec["identical_model_input"]:
        out.append("delegate but the model input differs from phase 1")
    for i in rec["intents"]:
        if i.get("expect") == "answer" and not all(i.get("gold_in_phase2_context", [True])):
            out.append(f"intent {i['index'] + 1}: gold evidence not shown to the model")
    return out


def _score(x) -> str:
    return "–" if x is None else f"{x:.1f}"


def _admission(i: dict) -> str:
    if i["outside_corpus"]:
        return "OUTSIDE CORPUS: " + ", ".join(i["outside_corpus"])
    return "adm" if i["admissible"] else "no-evidence"


def write_md(p2: list[dict], integ: list[dict]) -> str:
    out = ["# Phase 2 offline evaluation (no language model)", "",
           "Real retrieval stack (hybrid → temporal → rerank), stub model; see evaluation/multi_intent/run.py. "
           "\"gold P1/P2\" = each intent's gold evidence in the context phase 1 / phase 2 shows the model.", "",
           f"## Phase 2 cases: {sum(not r['checks'] for r in p2)}/{len(p2)} pass the offline checks", "",
           "| case | multi | strategy | calls P1→P2 | identical input | intents: covered / admissible (best score) / gold P1→P2 | checks |",
           "|---|---|---|---|---|---|---|"]
    for r in p2:
        parts = []
        for i in r["intents"] if r["multi"] else []:
            g1 = "".join("✓" if x else "✗" for x in i.get("gold_in_phase1_context", [])) or "–"
            g2 = "".join("✓" if x else "✗" for x in i.get("gold_in_phase2_context", [])) or "–"
            parts.append(f"{i['index'] + 1}: {'cov' if i['covered_by_question_context'] else 'NEW'} / "
                         f"{_admission(i)} ({_score(i['best_rerank'])}) / {g1}→{g2}")
        out.append(f"| {r['id']} | {r['multi']} | {r['strategy']} | {r['phase1_calls']}→{r['phase2_calls']} | "
                   f"{r['identical_model_input']} | {'<br>'.join(parts) or '—'} | {'; '.join(r['checks']) or 'ok'} |")
    changed = [r for r in integ if not r["identical_model_input"]]
    out += ["", f"## The 27 integration questions (read only): {sum(r['multi'] for r in integ)} decomposed, "
                f"{sum(r['strategy'] == 'delegate' for r in integ)} delegate to phase 1, "
                f"{len(integ) - len(changed)}/{len(integ)} with model input identical to phase 1", "",
            "| question | multi | strategy | identical input | reason |", "|---|---|---|---|---|"]
    for r in integ:
        if r["multi"] or not r["identical_model_input"]:
            out.append(f"| {r['id']} | {r['multi']} | {r['strategy']} | {r['identical_model_input']} | {r['reason']} |")
    out.append(f"\nAll other integration questions: single intent, delegate, identical model input.")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    import os

    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from adaptive.multi.decompose import load_aliases
    from chunking.pipeline import load_chunks
    from evaluation.integration.cases import CASES as INTEGRATION
    from evaluation.multi_intent.cases import CASES
    from generation.pipeline import load_stack

    stack = load_stack(rerank=True)
    cbd = load_chunks()
    orgs = {c.organization for c in stack.chunks}
    unknown = {a: o for a, o in load_aliases().items() if o not in orgs}
    if unknown:
        print(f"organization aliases name organizations missing from the corpus: {unknown}")
        return 2
    p2 = []
    for case in CASES:
        rec = evaluate(case["question"], stack, cbd, case)
        rec["checks"] = checks(rec)
        p2.append(rec)
        print(f"{case['id']:22} multi={rec['multi']!s:5} {rec['strategy']:10} calls {rec['phase1_calls']}->"
              f"{rec['phase2_calls']} identical={rec['identical_model_input']!s:5} {'; '.join(rec['checks']) or 'ok'}",
              flush=True)
    integ = []
    for case in INTEGRATION:
        rec = evaluate(case["question"], stack, cbd) | {"id": case["id"]}
        integ.append(rec)
        print(f"{case['id']:22} multi={rec['multi']!s:5} {rec['strategy']:10} identical={rec['identical_model_input']}",
              flush=True)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "offline.json").write_text(json.dumps({"phase2_cases": p2, "integration_questions": integ}, indent=1,
                                                     ensure_ascii=False) + "\n", encoding="utf-8")
    md = write_md(p2, integ)
    (RESULTS / "offline.md").write_text(md, encoding="utf-8")
    print(md)
    return 1 if any(r["checks"] for r in p2) else 0


if __name__ == "__main__":
    raise SystemExit(main())
