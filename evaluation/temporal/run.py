"""Temporal evaluation: rankings before vs after version resolution.

  python -m evaluation.temporal.run

Methods (existing retrieval unchanged):
  hybrid            vs  hybrid + temporal
  hybrid + rerank   vs  temporal + rerank (resolution before the reranker's 20-candidate pool)
Writes evaluation/temporal/results/temporal.json and results/report.md.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .cases import CASES, SERIES, VERSION_DOC, resolve_ref

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
TOP_K = 10


def first_gold_rank(top: list[str], units: list[set[str]]) -> int | None:
    ranks = [min((top.index(c) + 1 for c in u if c in top), default=None) for u in units]
    return None if not units or any(r is None for r in ranks) else max(ranks)  # rank at which ALL units are present


def evaluate(case, before: list[str], after: list[str], res, chunks_by_doc, doc_of) -> dict:
    units = [{resolve_ref(r, chunks_by_doc) for r in u} for u in case["gold"]]
    other = {resolve_ref(r, chunks_by_doc) for r in case["other"]}
    top_b, top_a = before[:TOP_K], after[:TOP_K]
    expected = sorted(VERSION_DOC[v] for v in case["versions"]) if case["versions"] is not None else None
    selected = res.selected.get(SERIES)
    series_docs = set(VERSION_DOC.values())

    def first_series_doc(top):
        return next((doc_of[c] for c in top if doc_of[c] in series_docs), None)

    return {
        "has_gold": bool(units),
        "intent": res.intent.kind, "intent_ok": res.intent.kind == case["intent"], "trigger": res.intent.trigger,
        "selected": selected, "selection_ok": (selected is None) if expected is None else (selected == expected),
        "flags": res.flags,
        "gold_rank_before": first_gold_rank(top_b, units), "gold_rank_after": first_gold_rank(top_a, units),
        "other_version_in_top10_before": sum(c in other for c in top_b),
        "other_version_in_top10_after": sum(c in other for c in top_a),
        "wrong_version_chunks_in_top10_before": sum(doc_of[c] in series_docs - set(expected or series_docs) for c in top_b),
        "wrong_version_chunks_in_top10_after": sum(doc_of[c] in series_docs - set(expected or series_docs) for c in top_a),
        "first_series_doc_before": first_series_doc(top_b), "first_series_doc_after": first_series_doc(top_a),
        "unchanged": top_a == top_b, "dropped": len(res.dropped),
        "top_before": top_b, "top_after": top_a,
    }


def run() -> dict:
    import torch

    torch.set_num_threads(8)
    from chunking.pipeline import load_chunks
    from generation.pipeline import load_stack
    from temporal.pipeline import hybrid_ranked, hybrid_temporal, reranked, temporal_reranked
    from temporal.resolve import TemporalResolver

    chunks_by_doc = load_chunks()
    chunks = [c for cs in chunks_by_doc.values() for c in cs]
    doc_of = {c.chunk_id: c.doc_id for c in chunks}
    stack = load_stack(rerank=True)
    resolver = TemporalResolver.from_chunks(chunks)
    out = []
    for case in CASES:
        q = case["query"]
        hb = hybrid_ranked(stack, q)
        ha, res = hybrid_temporal(stack, resolver, q)
        rb = reranked(stack, q)
        ra, _ = temporal_reranked(stack, resolver, q)
        out.append({"id": case["id"], "category": case["category"], "query": q,
                    "hybrid": evaluate(case, hb, ha, res, chunks_by_doc, doc_of),
                    "rerank": evaluate(case, rb, ra, res, chunks_by_doc, doc_of)})
    return {"cases": out}


def write_report(result: dict) -> str:
    rows = result["cases"]
    n = len(rows)
    out = ["# Temporal / version resolution evaluation", "",
           f"{n} cases (UConn Travel and Entertainment Procedures, February vs July 2026). Separate from the frozen "
           "retrieval benchmark. Gold rank = rank at which all gold evidence units are in the top 10 (— = not all present).", ""]
    intent_ok = sum(r["hybrid"]["intent_ok"] for r in rows)
    sel_ok = sum(r["hybrid"]["selection_ok"] for r in rows)
    out += [f"* Intent detected correctly: **{intent_ok}/{n}**; requested version selected correctly: **{sel_ok}/{n}**.", ""]
    for m, label in (("hybrid", "Hybrid RRF"), ("rerank", "Hybrid + rerank")):
        gold_rows = [r for r in rows if r[m]["has_gold"]]
        g1b = sum(r[m]["gold_rank_before"] == 1 for r in gold_rows)
        g1a = sum(r[m]["gold_rank_after"] == 1 for r in gold_rows)
        g10b = sum(r[m]["gold_rank_before"] is not None for r in gold_rows)
        g10a = sum(r[m]["gold_rank_after"] is not None for r in gold_rows)
        wb = sum(r[m]["wrong_version_chunks_in_top10_before"] > 0 for r in rows)
        wa = sum(r[m]["wrong_version_chunks_in_top10_after"] > 0 for r in rows)
        neu = [r for r in rows if r["category"] == "neutral"]
        out += [f"## {label}: before → after", "",
                f"* cases with any wrong-version chunk in the top 10: {wb} → {wa}",
                f"* gold evidence at rank 1: {g1b} → {g1a}; all gold units in top 10: {g10b} → {g10a} (of {len(gold_rows)} cases with gold)",
                f"* neutral cases with identical top 10: {sum(r[m]['unchanged'] for r in neu)}/{len(neu)}", "",
                "| case | category | intent | selected | gold rank | wrong-version chunks in top 10 | first procedures version in top 10 |",
                "|---|---|---|---|---|---|---|"]
        for r in rows:
            e = r[m]
            sel = ",".join("feb" if "final" in d else "jul" for d in e["selected"]) if e["selected"] is not None else "—"
            if e["selected"] == []:
                sel = "none (unavailable)"
            fs = lambda d: "—" if d is None else ("feb" if "final" in d else "jul")  # noqa: E731
            out.append(f"| {r['id']} | {r['category']} | {e['intent']}{'' if e['intent_ok'] else ' ✗'} | {sel}{'' if e['selection_ok'] else ' ✗'} | "
                       f"{e['gold_rank_before'] or '—'} → {e['gold_rank_after'] or '—'} | "
                       f"{e['wrong_version_chunks_in_top10_before']} → {e['wrong_version_chunks_in_top10_after']} | "
                       f"{fs(e['first_series_doc_before'])} → {fs(e['first_series_doc_after'])} |")
        out.append("")
    report = "\n".join(out) + "\n"
    (RESULTS / "report.md").write_text(report, encoding="utf-8")
    return report


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    result = run()
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "temporal.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(write_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
