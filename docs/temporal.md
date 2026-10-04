# Temporal / version-aware resolution

A post-retrieval layer (`temporal/`) that chooses between versions of the same document using only
chunk metadata: `series_id`, `effective_date`, `superseded_date`, `is_current`. The corpus, chunks,
retrieval (dense, BM25, RRF), the reranker and generation are unchanged. The layer filters ranked
candidate lists.

```
query -> hybrid.fuse (unchanged) -> temporal.resolve -> [top 20 -> reranker.rerank (unchanged)] -> top k
python -m evaluation.temporal.run      # before/after evaluation (18 cases)
```

## Query intent (`temporal/intent.py`, deterministic patterns)

| intent | trigger | examples |
|---|---|---|
| point_in_time | one date or month named | "effective July 1, 2026", "the February 2026 procedures", "on March 15, 2026", `2026-07-01`, `7/1/2026` |
| compare | two or more different dates/months | "between the February 2026 and July 2026 procedures" |
| current | no date; "current", "currently", "latest", "now", "today", "in effect today", "up to date" | "UConn's current rule" |
| neutral | anything else (a bare year is too coarse) | "What trip cost needs SIO approval?" |

A named date takes precedence over "current".

## Version selection (`temporal/versions.py`)

Only series with two or more versions are resolved (currently `uconn-travel-entertainment-procedures`:
February 1, 2026 and July 1, 2026). Versions are identified by `series_id` + `doc_id`, never by filename.

* **Full date d:** the version in force on d: `effective_date <= d` and (`superseded_date` missing or `d < superseded_date`).
* **Month m:** the version whose `effective_date` falls in m (the month names that version); otherwise the version in force on the 1st of m.
* **Compare:** the union of the versions selected for each named date.
* **Current:** the version with `is_current == true`; if not exactly one, no filtering (flag `current_version_ambiguous`).
* **No version matches** (e.g. a date before the earliest version): all versions of the series are removed and the
  flag `requested_version_unavailable` is set. No version is presented as the one requested.

## Resolution (`temporal/resolve.py`)

A stable filter over the ranked list: chunks from non-selected versions of a resolved series are removed.
All other chunks, including other documents and single-version documents like the UConn *policy*, keep
their relative order. Neutral queries return the list unchanged. With reranking, resolution runs before
the reranker's fixed 20-candidate pool is formed (`temporal/pipeline.py`).

## Evaluation (`evaluation/temporal/`)

18 cases, separate from the frozen retrieval benchmark: explicit July (4), explicit February (3), current (3),
neutral (3), conflicts between versions (2), only one version contains the evidence (3, including a date no
version covers). The run reports intent, selected version, gold rank before/after, wrong-version chunks in the
top 10 before/after, and which procedures version ranks first. Results: `evaluation/temporal/results/`.

## Limitations

* Pattern-based intent: relative phrasing ("last spring", "before the July change", "the old rules") is not recognized
  and is treated as neutral.
* Any named date resolves the UConn procedures series even if the question is about another organization's rule
  on that date; this only removes UConn chunks from a version not in force on that date.
* Resolution removes wrong-version chunks but does not re-rank: if retrieval or the reranker ranks the right
  version's chunk low, it stays low.
