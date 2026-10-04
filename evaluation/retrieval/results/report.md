# Dense retrieval: embedding model evaluation

Benchmark: retrieval-bench-1, 69 cases. Device: cpu (Intel64 Family 6 Model 151 Stepping 2, GenuineIntel), 8 threads, torch 2.14.0+cpu, sentence-transformers 6.1.0. Top-k = 10, exact cosine search.

## Models

| model | repo @ revision | params (M) | weights (MB) | dim | max tokens | tokenizer | prefixes |
|---|---|---|---|---|---|---|---|
| arctic-m | Snowflake/snowflake-arctic-embed-m-v1.5 @ e58a8f7561 | 108.9 | 415.4 | 768 | 512 | BertTokenizer (vocab 30522) | q:`Represent this sentence for searching relevant passages:` p:`` |
| bge-base | BAAI/bge-base-en-v1.5 @ a5beb1e3e6 | 109.5 | 417.7 | 768 | 512 | BertTokenizer (vocab 30522) | q:`Represent this sentence for searching relevant passages:` p:`` |
| bge-small | BAAI/bge-small-en-v1.5 @ 5c38ec7c40 | 33.4 | 127.3 | 384 | 512 | BertTokenizer (vocab 30522) | q:`Represent this sentence for searching relevant passages:` p:`` |
| e5-base | intfloat/e5-base-v2 @ f52bf8ec8c | 109.5 | 417.7 | 768 | 512 | BertTokenizer (vocab 30522) | q:`query:` p:`passage:` |
| minilm-l6 | sentence-transformers/all-MiniLM-L6-v2 @ 1110a243fd | 22.7 | 86.7 | 384 | 256 | BertTokenizer (vocab 30522) | none |

## Retrieval quality (all cases)

| model | R@1 | R@3 | R@5 | R@10 | MRR@10 | AllUnits@10 |
|---|---|---|---|---|---|---|
| arctic-m | 0.768 | 0.920 | 0.986 | 0.986 | 0.867 | 0.986 |
| bge-base | 0.746 | 0.920 | 0.949 | 0.978 | 0.837 | 0.971 |
| bge-small | 0.732 | 0.913 | 0.927 | 0.964 | 0.835 | 0.957 |
| e5-base | 0.703 | 0.862 | 0.942 | 0.971 | 0.811 | 0.971 |
| minilm-l6 | 0.652 | 0.899 | 0.920 | 0.978 | 0.786 | 0.971 |

## By question type (Recall@5 / MRR@10)

| type | n | arctic-m | bge-base | bge-small | e5-base | minilm-l6 |
|---|---|---|---|---|---|---|
| direct_fact | 3 | 0.67 / 0.67 | 0.67 / 0.71 | 0.67 / 0.50 | 0.67 / 0.67 | 0.67 / 0.72 |
| paraphrase | 6 | 1.00 / 0.51 | 1.00 / 0.47 | 1.00 / 0.81 | 0.83 / 0.39 | 0.67 / 0.44 |
| conditional | 12 | 1.00 / 0.96 | 1.00 / 1.00 | 1.00 / 0.92 | 1.00 / 0.96 | 1.00 / 0.94 |
| exception | 7 | 1.00 / 0.86 | 1.00 / 1.00 | 1.00 / 0.90 | 0.86 / 0.81 | 1.00 / 0.86 |
| numeric_threshold | 26 | 1.00 / 1.00 | 1.00 / 0.96 | 1.00 / 0.94 | 1.00 / 1.00 | 1.00 / 0.89 |
| terminology | 5 | 1.00 / 0.60 | 0.60 / 0.47 | 0.60 / 0.50 | 0.80 / 0.45 | 0.60 / 0.36 |
| clause_specific | 2 | 1.00 / 1.00 | 1.00 / 0.75 | 1.00 / 0.62 | 1.00 / 0.62 | 1.00 / 0.67 |
| cross_section | 2 | 1.00 / 1.00 | 0.75 / 0.62 | 0.50 / 0.75 | 1.00 / 0.75 | 0.75 / 1.00 |
| version_sensitive | 6 | 1.00 / 0.71 | 1.00 / 0.61 | 0.83 / 0.69 | 1.00 / 0.57 | 1.00 / 0.67 |
| +numeric_threshold | 2 | 1.00 / 0.62 | 1.00 / 0.67 | 1.00 / 1.00 | 0.50 / 0.58 | 1.00 / 0.67 |

## Resources (CPU)

| model | load s | index 466 chunks s | chunks/s | query ms (mean / p95) | RSS after load MB | peak RSS indexing MB | index KB |
|---|---|---|---|---|---|---|---|
| arctic-m | 18.1 | 201.652 | 2.3 | 99.2 / 116.4 | 846 | 1147 | 1398 |
| bge-base | 18.08 | 193.526 | 2.4 | 111.8 / 150.8 | 851 | 1149 | 1398 |
| bge-small | 19.85 | 63.636 | 7.3 | 54.0 / 63.8 | 562 | 760 | 699 |
| e5-base | 18.29 | 175.308 | 2.7 | 88.8 / 108.1 | 853 | 1172 | 1398 |
| minilm-l6 | 23.19 | 28.231 | 16.5 | 12.2 / 19.1 | 520 | 636 | 699 |

## Tokenization

| model | max passage tokens | median | truncated chunks | model tokens per estimated token |
|---|---|---|---|---|
| arctic-m | 420 | 175 | 0 | 1.073 |
| bge-base | 420 | 175 | 0 | 1.073 |
| bge-small | 420 | 175 | 0 | 1.073 |
| e5-base | 422 | 177 | 0 | 1.089 |
| minilm-l6 | 420 | 175 | 123 | 1.073 |

## Version-sensitive cases (UConn procedures)

Rank of the expected version's chunk vs the same section of the other version.

| case | expected | arctic-m | bge-base | bge-small | e5-base | minilm-l6 |
|---|---|---|---|---|---|---|
| uv-01 | 2026-07-01 | 1 vs 60 | 3 vs 145 | 1 vs 130 | 1 vs 136 | 2 vs 66 |
| uv-02 | 2026-02-01 | 1 vs 2 | 1 vs 2 | 1 vs 2 | 5 vs 6 | 2 vs 1 |
| uv-03 | 2026-07-01 | 1 vs 2 | 2 vs 1 | 1 vs 2 | 1 vs 2 | 1 vs 2 |
| uv-04 | 2026-02-01 | 2 vs — | 2 vs — | 2 vs — | 2 vs — | 2 vs — |
| uv-05 | 2026-07-01 | 4 vs 5 | 3 vs 5 | 6 vs 10 | 5 vs 8 | 1 vs 4 |
| uv-06 | 2026-02-01 | 2 vs 1 | 1 vs 2 | 2 vs 1 | 2 vs 1 | 2 vs 1 |

## Misses

Cases where a model did not retrieve every evidence unit within the top 10 (✗ = no unit found, ◐ = some units found).

| case | type | arctic-m | bge-base | bge-small | e5-base | minilm-l6 |
|---|---|---|---|---|---|---|
| ut-05 | direct_fact | ✗ | ✓ | ✗ | ✗ | ✓ |
| ut-06 | terminology | ✓ | ✗ | ✗ | ✗ | ✗ |
| uc-08 | cross_section | ✓ | ◐ | ◐ | ✓ | ◐ |

