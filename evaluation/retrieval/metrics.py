"""Retrieval metrics over the benchmark. Pure functions: ranked chunk ids in, numbers out.

Relevance rule (see README.md):
  * A case has one or more evidence units; each unit lists acceptable chunk ids (sources).
  * A unit is *found at k* if any of its sources is among the top-k results.
  * Recall@k (per case) = found units / all units. For single-unit cases this is a 0/1 hit.
  * AllUnits@k (per case) = 1 only if every unit is found (strict; differs only for cross_section).
  * MRR@10 (per case) = 1 / rank of the first result that is a source of any unit, 0 if none in the top 10.
Corpus-level numbers are unweighted means over cases.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean

KS = (1, 3, 5, 10)


def unit_ranks(case: dict, ranked: list[str]) -> list[int | None]:
    pos = {cid: i for i, cid in enumerate(ranked, 1)}
    out = []
    for u in case["units"]:
        ranks = [pos[s["chunk_id"]] for s in u["sources"] if s["chunk_id"] in pos]
        out.append(min(ranks) if ranks else None)
    return out


def case_metrics(case: dict, ranked: list[str]) -> dict:
    ranks = unit_ranks(case, ranked)
    n = len(ranks)
    m = {}
    for k in KS:
        found = sum(1 for r in ranks if r is not None and r <= k)
        m[f"recall@{k}"] = found / n
        m[f"all_units@{k}"] = float(found == n)
    first = min((r for r in ranks if r is not None and r <= 10), default=None)
    m["mrr@10"] = 1 / first if first else 0.0
    m["first_gold_rank"] = first
    m["unit_ranks"] = ranks
    return m


def aggregate(per_case: list[dict], cases: list[dict]) -> dict:
    def summarize(rows: list[dict]) -> dict:
        keys = [f"recall@{k}" for k in KS] + [f"all_units@{k}" for k in KS] + ["mrr@10"]
        return {"n": len(rows), **{k: round(mean(r[k] for r in rows), 4) for k in keys}} if rows else {"n": 0}

    by_type: dict[str, list[dict]] = defaultdict(list)
    for case, m in zip(cases, per_case):
        by_type[case["question_type"]].append(m)
        for t in case.get("secondary_types", []):
            by_type[f"+{t}"].append(m)  # secondary tags, reported separately (overlapping)
    return {"overall": summarize(per_case), "by_type": {t: summarize(rows) for t, rows in sorted(by_type.items())}}
