"""Refinement of an answer by a late constraint (AdaptiveRAG phase 3): "refine, do not restart". No model here.

  delta = controller.multi.answer(resolution.query, targeted_retrieval, REFINED, reason)     # the constraint's own
  v2, record = merge(v1, delta, chunks_by_id, constraint)                                   # answer v1 + delta

Answer v1 is the stored answer of the turn that is refined; it is read, never changed. `delta` is the answer to
the same question under the constraint, from the evidence retrieved for the constraint alone. Each claim of v1 is

  dropped    its evidence is no longer valid: a cited chunk is not in the corpus, or belongs to a version of a
             document that the constraint deselected ("Use the July 2026 version instead.");
  replaced   a claim of the delta states the same thing under the constraint: their texts share at least OVERLAP
             of their content words (the same rule with another number, limit or condition). The new claim takes
             its place; both are never kept;
  kept       otherwise, with its quotes and its citations.

Delta claims that replace nothing are added after them. Citations are renumbered in the order of the text. Then the
whole of answer v2 is verified again by the unchanged verifier (generation.validate.verify) against the evidence it
cites, the kept chunks and the new ones: a kept claim that does not pass is dropped, so nothing is in v2 because it
was in v1. If the delta's own claims do not pass in that context, v2 is the delta alone.

The text of v2 is its claims in order, written as every answer text is, then the delta's statement of what the
sources do not state. When the delta found nothing for the constraint, the kept claims stand and that is stated.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace

from adaptive.multi.controller import MultiIntentAnswer
from generation.answer import NOT_IN_SOURCES_PREFIX
from generation.context import Evidence, assemble
from generation.validate import verify

from .gate import _sentence

REFINED = "refined"  # MultiIntentAnswer.strategy of an answer v2
OVERLAP = 0.5  # shared content words / all content words of two claim texts, from which they state the same thing

_WORD = re.compile(r"[a-z0-9]+(?:[.,]\d+)*")
_STOP = set("a an the of for to in on at by with from as and or but is are was were be been must may can will shall "
            "should that this these those it its their his her not no if when than then which who whom per any all "
            "each such".split())


def _content(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def same_statement(a: str, b: str) -> bool:
    """Two claim texts state the same thing, possibly with different values: OVERLAP of their content words agree."""
    x, y = _content(a), _content(b)
    return bool(x and y) and len(x & y) / len(x | y) >= OVERLAP


def _cited(claim: dict, citations: dict) -> list:
    return [citations[n] for n in claim["citations"] if n in citations]


def merge(v1: MultiIntentAnswer | None, delta: MultiIntentAnswer, chunks: dict, constraint: str
          ) -> tuple[MultiIntentAnswer, dict]:
    """Answer v2 and the record of what happened to each claim of v1 (see the module docstring)."""
    selected = (delta.intents[0].get("selected_versions") or {}) if delta.intents else {}
    old = {c.number: c for c in (v1.citations if v1 else [])}
    new = {c.number: c for c in delta.citations}
    fresh = [dict(cl) for cl in delta.claims]
    record = {"kept": [], "replaced": [], "dropped": [], "added": []}

    def invalid(claim: dict) -> str | None:
        cited = _cited(claim, old)
        if not cited:
            return "it cites no source"
        for c in cited:
            chunk = chunks.get(c.chunk_id)
            if chunk is None:
                return f"its source {c.chunk_id} is not in the corpus"
            if chunk.series_id in selected and chunk.doc_id not in selected[chunk.series_id]:
                return f"its source is a version the constraint deselected ({chunk.doc_id})"
        return None

    plan: list[tuple[str, dict, dict]] = []  # (origin, claim, its citations by number)
    used: set[int] = set()
    for claim in (v1.claims if v1 else []):
        why = invalid(claim)
        if why:
            record["dropped"].append({"claim": claim["text"], "why": why})
            continue
        k = next((i for i, d in enumerate(fresh) if same_statement(claim["text"], d["text"])), None)
        if k is None:
            plan.append(("kept", claim, old))
        else:
            record["replaced"].append({"claim": claim["text"], "by": fresh[k]["text"]})
            if k not in used:  # the new claim stands where the first claim it replaces stood
                used.add(k)
                plan.append(("replaces", fresh[k], new))
    plan += [("new", d, new) for i, d in enumerate(fresh) if i not in used]

    def build(entries):
        """Claims and citations of v2, renumbered in the order of the text; and the verifier's verdict on them."""
        numbers: dict[str, int] = {}
        citations, claims = [], []
        for origin, claim, by_number in entries:
            marks = []
            for c in _cited(claim, by_number):
                if c.chunk_id not in numbers:
                    numbers[c.chunk_id] = len(numbers) + 1
                    citations.append(replace(c, number=numbers[c.chunk_id], label=f"S{numbers[c.chunk_id]}"))
                marks.append(numbers[c.chunk_id])
            claims.append(claim | {"citations": list(dict.fromkeys(marks)), "intents": [0], "refinement": origin})
        evidence = [Evidence(chunks[c.chunk_id], c.number) for c in citations if c.chunk_id in chunks]
        ctx = assemble(evidence, max_sources=max(len(evidence), 1), max_tokens=10 ** 9)  # every cited chunk, whole
        raw = json.dumps({"status": "answered", "not_in_sources": [], "abstention_reason": "", "claims": [
            {"text": cl["text"], "sources": [f"S{n}" for n in cl["citations"]], "quotes": cl.get("quotes", [])}
            for cl in claims]})
        return claims, citations, verify(raw, ctx, delta.question, selected)

    def failing(problems: list[str]) -> set[int]:
        return {int(m.group(1)) - 1 for p in problems for m in [re.match(r"claim (\d+):", p)] if m}

    claims, citations, verdict = build(plan) if plan else ([], [], None)
    if plan and not verdict.ok:  # a kept claim that the verifier does not accept on the merged evidence goes
        bad = failing(verdict.problems)
        for i in sorted(bad):
            if i < len(plan) and plan[i][0] == "kept":
                problem = next(p for p in verdict.problems if p.startswith(f"claim {i + 1}:"))
                record["dropped"].append({"claim": plan[i][1]["text"], "why": f"not verified on the merged evidence: {problem}"})
        plan = [e for i, e in enumerate(plan) if not (i in bad and e[0] == "kept")]
        claims, citations, verdict = build(plan) if plan else ([], [], None)
        if plan and not verdict.ok:  # the delta was verified on its own evidence: it stands alone
            record["merge_rejected"] = verdict.problems
            plan = [e for e in plan if e[0] != "kept"]
            claims, citations, verdict = build(plan) if plan else ([], [], None)
            if plan and not verdict.ok:
                return delta, record | {"kept": [], "added": [d["text"] for d in fresh]}
    record["kept"] = [cl["text"] for cl in claims if cl["refinement"] == "kept"]
    record["added"] = [cl["text"] for cl in claims if cl["refinement"] == "new"]
    if not claims:  # nothing of v1 stands and the constraint found nothing: the delta's own outcome
        return delta, record
    gaps = list(delta.not_in_sources)
    if delta.status != "answered":
        gaps = [f"anything specific to the refinement ({constraint})"]
    record["gaps"] = gaps
    text = " ".join(_sentence(cl) for cl in claims)
    if gaps:
        text += " " + NOT_IN_SOURCES_PREFIX + "; ".join(g.rstrip(".") for g in gaps) + "."
    shown = {c.chunk_id for c in citations}
    evidence_intents = {cid: entries for cid, entries in (v1.evidence_intents if v1 else {}).items() if cid in shown}
    evidence_intents |= delta.evidence_intents
    by_number = {c.number: c.chunk_id for c in citations}

    def sources(*origins: str) -> list[str]:
        return list(dict.fromkeys(by_number[n] for cl in claims if cl["refinement"] in origins for n in cl["citations"]))

    # the evidence of v2: what its kept claims cite (preserved from v1), and what its changed and new claims cite
    record["evidence"] = {"preserved": sources("kept"), "new": sources("replaces", "new")}
    cited = sorted({n for cl in claims for n in cl["citations"]})
    intents = [i | {"citations": cited, "status": "answered"} for i in delta.intents]
    reason = (f"{delta.reason}; {len(record['kept'])} claim(s) kept, {len(record['replaced'])} replaced, "
              f"{len(record['dropped'])} dropped, {len(record['added'])} added")
    return replace(delta, status="answered", text=text, strategy=REFINED, reason=reason, citations=citations,
                   claims=claims, intents=intents, not_in_sources=gaps, abstention_reason=None, abstention_detail="",
                   evidence_intents=evidence_intents), record
