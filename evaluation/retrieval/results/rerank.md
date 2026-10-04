# Cross-encoder reranking

Reranker cross-encoder/ms-marco-MiniLM-L-6-v2 @ 233902d25c (22.7M params, max 512 tokens), candidate pool = top 20 of hybrid RRF (arctic-m + BM25, k=60). Benchmark: 69 cases, top-10. Consistency: dense-arctic-m: identical to saved rankings; hybrid-rrf: identical to saved rankings.

## Overall

| method | R@1 | R@3 | R@5 | R@10 | MRR@10 | AllUnits@10 |
|---|---|---|---|---|---|---|
| dense-arctic-m | 0.768 | 0.920 | 0.986 | 0.986 | 0.867 | 0.986 |
| hybrid-rrf | 0.739 | 0.899 | 0.971 | 1.000 | 0.841 | 1.000 |
| hybrid-rrf-rerank | 0.768 | 0.891 | 0.971 | 0.986 | 0.854 | 0.986 |

## By question type: Recall@1

| type | n | dense-arctic-m | hybrid-rrf | hybrid-rrf-rerank |
|---|---|---|---|---|
| direct_fact | 3 | 0.67 | 0.67 | 0.67 |
| paraphrase | 6 | 0.17 | 0.50 | 0.50 |
| conditional | 12 | 0.92 | 0.92 | 1.00 |
| exception | 7 | 0.71 | 0.86 | 0.86 |
| numeric_threshold | 26 | 1.00 | 0.85 | 0.92 |
| terminology | 5 | 0.40 | 0.40 | 0.20 |
| clause_specific | 2 | 1.00 | 1.00 | 1.00 |
| cross_section | 2 | 0.50 | 0.50 | 0.50 |
| version_sensitive | 6 | 0.50 | 0.33 | 0.33 |

## By question type: Recall@3

| type | n | dense-arctic-m | hybrid-rrf | hybrid-rrf-rerank |
|---|---|---|---|---|
| direct_fact | 3 | 0.67 | 0.67 | 0.67 |
| paraphrase | 6 | 0.83 | 0.83 | 0.83 |
| conditional | 12 | 1.00 | 1.00 | 1.00 |
| exception | 7 | 1.00 | 1.00 | 0.86 |
| numeric_threshold | 26 | 1.00 | 0.92 | 0.96 |
| terminology | 5 | 0.60 | 0.80 | 0.60 |
| clause_specific | 2 | 1.00 | 1.00 | 1.00 |
| cross_section | 2 | 0.75 | 1.00 | 0.75 |
| version_sensitive | 6 | 0.83 | 0.67 | 0.83 |

## By question type: MRR@10

| type | n | dense-arctic-m | hybrid-rrf | hybrid-rrf-rerank |
|---|---|---|---|---|
| direct_fact | 3 | 0.67 | 0.70 | 0.73 |
| paraphrase | 6 | 0.51 | 0.63 | 0.68 |
| conditional | 12 | 0.96 | 0.96 | 1.00 |
| exception | 7 | 0.86 | 0.93 | 0.89 |
| numeric_threshold | 26 | 1.00 | 0.90 | 0.95 |
| terminology | 5 | 0.60 | 0.64 | 0.42 |
| clause_specific | 2 | 1.00 | 1.00 | 1.00 |
| cross_section | 2 | 1.00 | 1.00 | 1.00 |
| version_sensitive | 6 | 0.71 | 0.58 | 0.59 |

## Movement of gold evidence (within the candidate pool)

* Case level (first gold chunk): moved up in 11, down in 9, unchanged in 49; no gold chunk in the pool for 0 (none).
* Chunk level (every gold chunk in a pool, 84 total): up 15, down 13, unchanged 56.

### Hybrid had the evidence in the top 10, reranking pushed (some of) it out

* ut-06 (terminology): hybrid rank 5, reranked >10; pool ranks (before→after) [('information-resources-use-and-security-policy-76b891::014-fe461e377f6f', 5, 13)]

### Reranking corrected a poor hybrid ranking (hybrid first gold > 3 → reranked ≤ 3)

* or-02 (paraphrase): 9 → 1
* ut-02 (numeric_threshold): 5 → 2
* uc-01 (numeric_threshold): 4 → 1
* uv-02 (version_sensitive): 4 → 2

### Reranking demoted a good hybrid ranking (hybrid first gold ≤ 3 → reranked > 3)

* ro-01 (numeric_threshold): 2 → 4
* uc-03 (terminology): 2 → 8
* uc-04 (exception): 1 → 4
* uc-09 (paraphrase): 1 → 4

## Per case: rank of the first gold chunk (cases where any method is not at rank 1)

| case | type | dense-arctic-m | hybrid-rrf | hybrid-rrf-rerank |
|---|---|---|---|---|
| or-02 | paraphrase | 4 | 9 | 1 |
| or-04 | terminology | 1 | 1 | 2 |
| pe-03 | paraphrase | 2 | 3 | 2 |
| st-03 | paraphrase | 3 | 1 | 3 |
| ya-02 | terminology | 2 | 2 | 2 |
| ro-01 | numeric_threshold | 1 | 2 | 4 |
| ro-05 | conditional | 2 | 1 | 1 |
| mi-02 | numeric_threshold | 1 | 2 | 1 |
| ru-05 | exception | 2 | 1 | 1 |
| ut-02 | numeric_threshold | 1 | 5 | 2 |
| ut-05 | direct_fact | — | 10 | 5 |
| ut-06 | terminology | 4 | 5 | — |
| ut-07 | paraphrase | 2 | 3 | 1 |
| ut-08 | exception | 2 | 2 | 1 |
| uc-01 | numeric_threshold | 1 | 4 | 1 |
| uc-02 | conditional | 1 | 2 | 1 |
| uc-03 | terminology | 4 | 2 | 8 |
| uc-04 | exception | 1 | 1 | 4 |
| uc-09 | paraphrase | 2 | 1 | 4 |
| uv-01 | version_sensitive | 1 | 1 | 3 |
| uv-02 | version_sensitive | 1 | 4 | 2 |
| uv-04 | version_sensitive | 2 | 2 | 1 |
| uv-05 | version_sensitive | 4 | 4 | 5 |
| uv-06 | version_sensitive | 2 | 2 | 2 |

— = no gold chunk in the top 10; ◐ = only some evidence units in the top 10.

## Misses (not every evidence unit in the top 10)

| case | type | dense-arctic-m | hybrid-rrf | hybrid-rrf-rerank |
|---|---|---|---|---|
| ut-05 | direct_fact | ✗ | ✓ | ✓ |
| ut-06 | terminology | ✓ | ✓ | ✗ |

## Runtime and memory (CPU)

* Reranker load: 0.43 s; RSS 852 → 950 MB (+97 MB, with arctic-m already loaded); peak RSS during evaluation 1155 MB.
* Per query (mean / p95 ms): dense 100.4 / 120.8; hybrid 102.8 / 127.4; candidate pool 104.7 / 136.1; cross-encoder on 20 pairs 2226.5 / 2645.8.
* Pairs scored: 1380; tokens per pair: median 209, max 440; truncated (> 512): 0.

