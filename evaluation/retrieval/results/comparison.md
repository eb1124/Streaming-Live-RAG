# BM25 vs dense vs hybrid (RRF)

Benchmark retrieval-bench-1, 69 cases, top-10. Dense = arctic-m. Hybrid = RRF(k=60) over the top 100 of each. Determinism check (dense vs saved run): identical top-10 rankings for all cases.

## Overall

| method | R@1 | R@3 | R@5 | R@10 | MRR@10 | AllUnits@10 | query ms (mean/p95) |
|---|---|---|---|---|---|---|---|
| bm25 | 0.674 | 0.862 | 0.891 | 0.957 | 0.783 | 0.957 | 1.62 / 2.61 |
| dense-arctic-m | 0.768 | 0.920 | 0.986 | 0.986 | 0.867 | 0.986 | 99.7 / 116.69 |
| hybrid-rrf | 0.739 | 0.899 | 0.971 | 1.000 | 0.841 | 1.000 | 105.1 / 130.5 |

## By question type: Recall@1

| type | n | bm25 | dense-arctic-m | hybrid-rrf |
|---|---|---|---|---|
| direct_fact | 3 | 0.67 | 0.67 | 0.67 |
| paraphrase | 6 | 0.50 | 0.17 | 0.50 |
| conditional | 12 | 0.92 | 0.92 | 0.92 |
| exception | 7 | 0.86 | 0.71 | 0.86 |
| numeric_threshold | 26 | 0.73 | 1.00 | 0.85 |
| terminology | 5 | 0.20 | 0.40 | 0.40 |
| clause_specific | 2 | 0.50 | 1.00 | 1.00 |
| cross_section | 2 | 0.25 | 0.50 | 0.50 |
| version_sensitive | 6 | 0.50 | 0.50 | 0.33 |

## By question type: Recall@5

| type | n | bm25 | dense-arctic-m | hybrid-rrf |
|---|---|---|---|---|
| direct_fact | 3 | 0.67 | 0.67 | 0.67 |
| paraphrase | 6 | 0.67 | 1.00 | 0.83 |
| conditional | 12 | 1.00 | 1.00 | 1.00 |
| exception | 7 | 1.00 | 1.00 | 1.00 |
| numeric_threshold | 26 | 0.92 | 1.00 | 1.00 |
| terminology | 5 | 0.80 | 1.00 | 1.00 |
| clause_specific | 2 | 1.00 | 1.00 | 1.00 |
| cross_section | 2 | 0.75 | 1.00 | 1.00 |
| version_sensitive | 6 | 0.83 | 1.00 | 1.00 |

## By question type: MRR@10

| type | n | bm25 | dense-arctic-m | hybrid-rrf |
|---|---|---|---|---|
| direct_fact | 3 | 0.70 | 0.67 | 0.70 |
| paraphrase | 6 | 0.60 | 0.51 | 0.63 |
| conditional | 12 | 0.96 | 0.96 | 0.96 |
| exception | 7 | 0.89 | 0.86 | 0.93 |
| numeric_threshold | 26 | 0.81 | 1.00 | 0.90 |
| terminology | 5 | 0.52 | 0.60 | 0.64 |
| clause_specific | 2 | 0.75 | 1.00 | 1.00 |
| cross_section | 2 | 0.75 | 1.00 | 1.00 |
| version_sensitive | 6 | 0.64 | 0.71 | 0.58 |

## Per case: rank of the first gold chunk (— = not in top 10)

| case | type | bm25 | dense-arctic-m | hybrid-rrf | hybrid: gold ranks in (dense, bm25) |
|---|---|---|---|---|---|
| or-02 | paraphrase | — | 4 | 9 | (4, 23) |
| or-04 | terminology | 2 | 1 | 1 | (1, 2) |
| or-07 | clause_specific | 2 | 1 | 1 | (1, 2) |
| pe-03 | paraphrase | 2 | 2 | 3 | (2, 2) |
| st-03 | paraphrase | 1 | 3 | 1 | (3, 1) |
| ya-02 | terminology | 2 | 2 | 2 | (2, 2) |
| ro-01 | numeric_threshold | 3 | 1 | 2 | (1, 3) |
| ro-05 | conditional | 1 | 2 | 1 | (2, 1) |
| mi-02 | numeric_threshold | 2 | 1 | 2 | (1, 2) |
| ru-01 | numeric_threshold | 3 | 1 | 1 | (1, 3) |
| ru-05 | exception | 1 | 2 | 1 | (25, 1) |
| mg-02 | numeric_threshold | 2 | 1 | 1 | (1, 2) |
| mg-05 | exception | 5 | 1 | 1 | (1, 5) |
| ut-02 | numeric_threshold | — | 1 | 5 | (1, 15) |
| ut-05 | direct_fact | 9 | — | 10 | (11, 9) |
| ut-06 | terminology | 10 | 4 | 5 | (4, 10) |
| ut-07 | paraphrase | 8 | 2 | 3 | (2, 8) |
| ut-08 | exception | 1 | 2 | 2 | (2, 1) |
| uc-01 | numeric_threshold | — | 1 | 4 | (1, 63) |
| uc-02 | conditional | 2 | 1 | 2 | (1, 2) |
| uc-03 | terminology | 2 | 4 | 2 | (4, 2) |
| uc-07 | cross_section | 2 | 1 | 1 | (1, 8) |
| uc-09 | paraphrase | 1 | 2 | 1 | (2, 1) |
| uv-02 | version_sensitive | 7 | 1 | 4 | (1, 7) |
| uv-04 | version_sensitive | 1 | 2 | 2 | (2, 1) |
| uv-05 | version_sensitive | 5 | 4 | 4 | (4, 5) |
| uv-06 | version_sensitive | 2 | 2 | 2 | (2, 2) |
| un-02 | numeric_threshold | 2 | 1 | 1 | (1, 2) |

Cases where every method ranked a gold chunk first are omitted. ◐ = only some evidence units in the top 10.

## Misses (not every evidence unit in the top 10)

| case | type | query | bm25 | dense-arctic-m | hybrid-rrf |
|---|---|---|---|---|---|
| or-02 | paraphrase | Oregon State needs to buy equipment expected to cost about $300,000. W | ✗ | ✓ | ✓ |
| ut-02 | numeric_threshold | How often must UT Austin user accounts be reviewed? | ✗ | ✓ | ✓ |
| ut-05 | direct_fact | Whom must UT Austin employees notify about an unauthorized disclosure  | ✓ | ✗ | ✓ |
| uc-01 | numeric_threshold | Above what amounts does UConn require receipts for travel expenses? | ✗ | ✓ | ✓ |
