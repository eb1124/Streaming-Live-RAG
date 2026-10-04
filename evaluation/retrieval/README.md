# Retrieval benchmark

A hand-built retrieval benchmark over the 12-document corpus. It lives here, separate from `corpus/`
and from generated data (`data/`), and is versioned with the code.

| File | Role |
|---|---|
| `cases.py` | **Source of truth.** The authored cases: query, type, evidence quotes, notes, review flags. |
| `build.py` | Resolves each evidence quote to the one chunk containing it and freezes `queries.jsonl`. |
| `queries.jsonl` | The frozen benchmark: header line (benchmark version, chunker version, corpus input hashes), then one case per line. |
| `metrics.py` | Metric definitions (pure functions). |
| `run.py`, `report.py` | Model evaluation and report (`results/<model>.json`, `results/report.md`). |

```
python -m evaluation.retrieval.build            # write queries.jsonl from cases.py
python -m evaluation.retrieval.build --check    # verify queries.jsonl against the current chunks (no writes)
python -m evaluation.retrieval.run              # evaluate all candidate models (CPU; one process per model)
python -m evaluation.retrieval.run --models bge-base --report-only
python -m retrieval.search "When is a UConn travel card suspended?" --model bge-base -k 5
```

## How cases were written

* Every question was written from the text of the chunks (`data/chunks`). Every answer is stated in
  the corpus, and each evidence unit is backed by a verbatim quote.
* Each case has one or more **evidence units** (the facts the answer needs). Each unit lists every
  chunk that states that fact (its **sources**). Examples: identical text in both UConn procedure
  versions, a rule stated in both the UConn policy and its procedures, or McGill's 30-day advance
  reconciliation, stated in PR4 and again in PR5.1.
* `build.py` fails if a quote matches no chunk, or more than one chunk, in its document. It also
  reports quotes that occur in documents *not* listed for the unit, so they can be reviewed. It
  never adds them to the gold set.
* Cases were fixed before any model was run. Gold labels are never changed because of what a model
  retrieves. A doubtful label gets a `review` note instead (see below).

## Question types

`direct_fact`, `paraphrase` (lay wording that avoids the document's terms), `conditional`,
`exception`, `numeric_threshold`, `terminology`, `clause_specific`, `cross_section` (the answer
needs two or more units from different sections or documents), `version_sensitive` (UConn
procedures, February vs July 2026). A case has one primary type and optional `secondary_types`.

## UConn versions

`version_sensitive` cases name the version in the question and set `expected_version`. Only that
version's chunk is gold, and retrieving the other version's chunk is a miss. Version-neutral UConn
cases (`un-*`, and some `uc-*`) accept either version, because the text is identical. The run also
records where the other version's matching section ranks (`version_check`). That is a diagnostic
for the later temporal-retrieval phase; dense retrieval has no notion of "current".

## Relevance rule and metrics

* A unit is **found at k** when any of its sources is in the top k results.
* **Recall@k** for a case = found units / all units. For single-unit cases (all but the
  `cross_section` ones) this is a 0/1 hit.
* **AllUnits@k** = 1 only if every unit is found (strict; differs from Recall@k only for multi-unit cases).
* **MRR@10** = 1 / rank of the first result that is a source of any unit (0 if none in the top 10).
* Reported values are unweighted means over cases, overall and by question type. Secondary types
  are reported separately with a `+` prefix; those groups overlap.

## Versioning and determinism

* `queries.jsonl` records `benchmark_version`, the chunker version and every document's
  ingestion content hash. Chunk ids are deterministic, so the benchmark stays valid as long as
  `build.py --check` passes (also run in the test suite).
* If chunking changes, `--check` fails. Rebuild only after checking that the new chunk for every
  quote is still the right evidence, and bump `BENCHMARK_VERSION`.
* Search is exact and breaks ties by index order. Models run on CPU with a fixed thread count at
  pinned revisions.

## Cases flagged for manual review

Flagged cases have a `review` field. They are evaluated normally; the flag records a doubt about
the label, not a change to it.

## BM25 and hybrid (RRF)

```
python -m evaluation.retrieval.compare          # BM25 vs dense (arctic-m) vs hybrid -> results/comparison.md, results/methods/*.json
python -m retrieval.search "..." --method bm25|dense|hybrid
```

* **BM25** (`retrieval/bm25.py`): Okapi BM25, k1 = 0.9, b = 0.4 (Pyserini defaults), Lucene idf.
  The analyzer lowercases text, joins thousands separators (`$7,500` → `7500`), keeps dotted numbers
  whole (`4.4.3.1`), drops Lucene's English stopwords and applies Snowball stemming. It indexes the
  same `retrieval_text` as the dense model.
* **Hybrid** (`retrieval/hybrid.py`): Reciprocal Rank Fusion of the arctic-m and BM25 rankings,
  rrf = Σ 1/(60 + rank), over the top 100 of each. Ties are broken by best component rank, then by
  corpus order.
* All parameters were fixed from standard defaults **before** evaluation and were not tuned on this
  benchmark. Tuning them here would leak the gold labels into the method. Any tuning needs a
  separate held-out set.
* `compare.py` checks that the dense rankings reproduce `results/arctic-m.json` exactly.

## Cross-encoder reranking

```
python -m evaluation.retrieval.rerank_eval      # dense vs hybrid vs hybrid + rerank -> results/rerank.md, results/methods/hybrid-rrf-rerank.json
```

* **Model:** `cross-encoder/ms-marco-MiniLM-L-6-v2` @ `233902d2`: 22.7M parameters, trained on MS MARCO
  passage ranking. It was chosen for CPU practicality on this memory-constrained machine; a 335M
  model paged heavily here. It takes `[CLS] query [SEP] passage [SEP]`, at most 512 word pieces. With
  this corpus the longest pair is 440 tokens, so nothing is truncated.
* **Pipeline:** hybrid RRF (unchanged) → top 20 candidates → the cross-encoder scores each (query,
  `retrieval_text`) pair → candidates are reordered by score, ties broken by hybrid rank.
* **Defaults, fixed before evaluation and not tuned:** a pool of 20 (it must exceed the reported top
  10), max length 512, batch 16.
* `rerank_eval.py` checks that dense and hybrid reproduce `results/methods/*.json` exactly. It also
  reports how the gold evidence moved, runtime and memory.
