# End-to-end integration evaluation, 2026-09-26

**Pipeline under test:** dense (arctic-m) + BM25 → RRF → temporal resolution → top-20 pool → cross-encoder rerank → top 10 → context assembly (max 6 sources) → Groq `openai/gpt-oss-20b` → deterministic verification → cited answer or abstention.

**Setup:**
* Everything uses the existing configuration: prompt `grounded-qa-1`, temperature 0, seed 1234, reasoning effort medium, max 4096 tokens.
* Nothing in the pipeline was modified or tuned before or after the runs.
* The suite has **27 new cases** in `evaluation/integration/cases.py`: 19 answerable and 8 where abstention is correct. It is separate from the retrieval (69), temporal (18) and generation (13) suites.
* The suite was run twice: `python -m evaluation.integration.run --runs 2`.

**Files:**
* `run-1.json`, `run-2.json`: every stage for every case (fused candidates with dense/BM25 ranks, temporal result, rerank pool and scores, rendered context, raw model output, verification problems, citations, provider usage and latency).
* `summary.md`: automatic checks only.
* `agreement.json`: run-to-run agreement.
* `manual_review.json`: my verdict for each case and run.

**What the automatic checks can and cannot show.** Automatic grounding checks are not evidence of correctness:
* "Passed verification" means only that the quotes exist in the cited sources and that the numbers appear there.
* Section 7 is a manual reading of every answer against its source.
* In this suite the verifier both passed a flawed answer and rejected correct ones (sections 4 and 7).

## 1. Retrieval / evidence

19 answerable cases need 23 gold evidence units in total.

| stage | units found |
|---|---|
| Hybrid RRF, top 10 | **23/23** (13 at rank 1) |
| 20-candidate rerank pool, after temporal filtering | 23/23 |
| Rerank top 10 | 23/23 |
| Assembled context | **23/23** |

No answerable case failed at retrieval, and none failed at context assembly.

## 2. Temporal / version correctness

* Intent was detected correctly in **27/27** cases, and the expected version was selected in **27/27**.
* **0** wrong-version chunks reached any context.
* Point-in-time and current-version cases dropped 21–24 chunks of the other version (43 in the no-version-available case).
* Version-neutral cases were left unchanged.
* `i-uc-unavailable` raised the flag `requested_version_unavailable`. The July 2026 *policy* chunk about advances stayed in context, and the model correctly did not present it as the 2025 procedures.
* The documented side effect occurred: in `i-mi-mileage`, the date named in a Michigan question resolved the UConn series to July and removed 11 February chunks. This did no harm here.

## 3. Reranking / evidence retention, and disagreement with hybrid ranking

* Gold units at rank 1: 13 in the post-temporal hybrid order and 14 after reranking.
* All 23 answerable units stayed in the top 10 and in the context.
* The reranker and the hybrid ranking chose a different top-1 chunk in **8/27** cases. Overlap of their top 10 lists ranged from 5 to 10 (median 8).
* Where the gold chunk moved:
  * **Moved up:** `i-uc-neu-sio` 3→1, `i-or-180k` unit 1 2→1, `i-uc-feb-reinstate` 2→1.
  * **Moved down, still in context:** `i-ru-advance` 1→3, `i-ut-clause` 1→2, `i-or-180k` unit 2 3→6, `i-uc-cmp-card` 4→5.
* **Earlier weakness, still present:** in `i-uc-march-tickets`, the February Air Travel chunk went from pool rank 4 to rerank rank **8**, which is outside the 6-source context. The outcome was still correct, because abstention was the right answer, so it was not a failure here.
* **Previous gap, closed:** `t-cmp-lodging` never had the July multi-bedroom rule in the top 10. In this suite's phrasing (`i-uc-cmp-lodging`) it ranked 3.
* The evidence gate (rerank logit < 0) fired once: `i-uc-nyc-hotel`, a correct abstention with no LLM call.

## 4. Grounded generation verification

| | run 1 | run 2 |
|---|---|---|
| Model outputs received | 25 | 25 |
| Accepted by verifier, answered | 13 | 12 |
| Model chose `insufficient_evidence` | 8 | 8 |
| **Rejected by verifier** | 4 | 5 |
| …of which the rejected content was substantively correct (manual) | **4/4** | **5/5** |

The verifier caught no wrong answer in either run, and it accepted the one flawed answer (`i-uc-neutral-card`, section 7). The 9 rejections have three causes:

* **The claim repeats a number from the question** (`i-or-180k` "$180,000", `i-pe-exception` "$75,000"). The numbers rule requires every number to appear in the cited source. 4 rejections, both runs.
* **The model writes U+2011 non-breaking hyphens in quotes** ("pre‑approved", "high‑risk"), and the verifier does not normalize them. `i-uc-spouse` run 2 and `i-ro-highrisk` run 1: 2 rejections.
* **The quote is not verbatim:**
  * `i-uc-neu-sio`, both runs: the model joined two non-adjacent bullet items into one quote.
  * `i-uc-personal-leg`, run 2: the chunk text contains a stray list marker ("business-only o segment"), and the model's clean quote failed to match it.

  3 rejections.

## 5. Citation correctness (answered outputs)

* Citations to chunks outside the context: **0**. Required citations missing: **0**.
* Wrong-version citations: **0**. Wrong-organization citations: **0**.
* Distractors were present in context but never cited. Examples: UConn's 20-day advance rule in `i-ru-advance`; Michigan, McGill and Rutgers mileage rates in `i-st-mileage`.

## 6. Abstention correctness

| | run 1 | run 2 |
|---|---|---|
| Cases where abstention is correct: abstained | **8/8** | **8/8** |
| Answered when it should have abstained | 0 | 0 |
| Answerable cases that abstained | 6 | 7 |
| …because of the provider (HTTP 429) | 1 | 1 |
| …because the verifier rejected the answer | 4 | 5 |
| …because the model chose `insufficient_evidence` | 1 | 1 |

In the table, the verifier rows cover the three `i-uc-neu-sio` / `i-or-180k` / `i-pe-exception` cases plus one U+2011 or list-marker case per run. The model's own abstention was `i-uc-cmp-lodging` (see section 7).

## 7. Actual answer correctness (manual review of all 54 outcomes)

Answerable cases (19 per run):

| verdict | run 1 | run 2 |
|---|---|---|
| Correct and substantively complete | **12** | **11** |
| Partial: versions merged | 1 | 1 |
| Substantively wrong | 0 | 0 |
| False abstention | 5 | 6 |
| Provider failure | 1 | 1 |

* **`i-uc-neutral-card` (the g-conflict question, end to end), both runs.** The answer was one merged sentence, "within 60 days of the transaction date or trip end date", citing both versions. It never says the versions differ, and it drops July's "fully approved" and "whichever is later" conditions. Every automatic check passed. When both versions are named (`i-uc-cmp-card`), the answer attributes each version correctly in both runs.
* **`i-uc-cmp-lodging`, both runs.** The model abstained because the February rule was absent, although the July rule was in context. This is cautious, but not the expected partial answer.
* **Earlier problems that did not recur:**
  * **`g-e2e-tips`:** `i-ru-tips` stated the "customary amount, 20% maximum" rule correctly in both runs. Both runs add "of the cost", which the source leaves implicit.
  * **`g-version-feb-trap`:** `i-uc-feb-reinstate` abstained correctly.
* **Regex checks vs substance:** two regex failures were correct in substance: `i-uc-cur-multibed` run 1 and `i-st-invoice` run 2.
* **Cases where abstention is correct:** 8/8 in both runs.

## 8. Provider / API failures

| run | case | error | wait before failure |
|---|---|---|---|
| 1 | `i-mi-mileage` | HTTP 429 | 9.5 s |
| 2 | `i-uc-cur-multibed` | HTTP 429 | 19.2 s |

* Both 429s hit the tokens-per-minute limit. The on-demand tier allows 8,000 tokens per minute, and a call uses about 2,300 tokens.
* Each error was raised after the SDK's own retries. The pipeline returned the `provider_error` abstention; nothing crashed.
* Most other calls were delayed by the same limit: 44 of the 50 successful calls took more than 5 s of wall time (section 10).

## 9. Run-to-run agreement

| aspect | agreement |
|---|---|
| Retrieval, temporal resolution, rerank order, assembled context | **27/27 identical** (fully deterministic) |
| Final status | 22/27 |
| Manual verdict | 22/27 |
| Citations | 22/27 |
| Identical answer text | 18/27 (12 of them are the fixed abstention text) |

The 5 disagreements:
* 2 are provider 429s: `i-uc-cur-multibed`, `i-mi-mileage`.
* 3 are verifier rejections of one run's quotes: `i-uc-spouse`, `i-uc-personal-leg`, `i-ro-highrisk`.

Of the 10 cases answered in both runs, all 10 were answer-equivalent with identical citations, and 6/10 had byte-identical text. Temperature 0 with a fixed seed does not make Groq reproducible. Non-determinism enters only at the LLM, and whether an answer survives verification depends on how the model happens to write its quotes.

## 10. Latency and tokens

Measured per case over 54 case-runs. Model loading is excluded.

| stage | median | p90 | max |
|---|---|---|---|
| Hybrid retrieval + RRF | 0.11 s | 0.14 s | 0.21 s |
| Temporal resolution | < 1 ms | | |
| Rerank (20 pairs, CPU) | 1.89 s | 2.42 s | 2.62 s |
| Context assembly | < 1 ms | | |
| LLM call, wall clock | 11.6 s | 21.5 s | 32.3 s |
| Case total | 14.6 s | 24.4 s | 35.3 s |

* **Groq server time was only 0.55 s** median (1.58 s max), with a median queue time of 0.33 s. The rest of the LLM wall time is client-side backoff from the tokens-per-minute limit.
* The "gate / assembly / verification" column in the raw files for these two runs (median 0.95 s) also includes the harness's 3 s pacing wait. The harness now records that wait separately, which applies only to future runs.
* **Tokens:**

  | run | successful calls | prompt | completion | of which reasoning | total |
  |---|---|---|---|---|---|
  | 1 | 25 | 49,434 | 11,244 | 8,565 | 60,678 |
  | 2 | 25 | 49,076 | 10,801 | 8,174 | 59,877 |

  A call uses about 2,300 tokens (median).

## Failed cases by stage

Answerable cases that were not answered correctly:

| case | run(s) | failing stage | cause |
|---|---|---|---|
| i-uc-neutral-card | 1, 2 | generation | merged the two versions; passed verification |
| i-uc-cmp-lodging | 1, 2 | generation | abstained although the July rule was in context |
| i-uc-neu-sio | 1, 2 | generation → verification | spliced, non-verbatim quote; correct rejection of a correct answer |
| i-or-180k | 1, 2 | verification | number from the question |
| i-pe-exception | 1, 2 | verification | number from the question |
| i-uc-spouse | 2 | verification | U+2011 hyphen not normalized |
| i-ro-highrisk | 1 | verification | U+2011 hyphen not normalized |
| i-uc-personal-leg | 2 | verification (source text artifact) | "business-only o segment" in chunk text |
| i-mi-mileage | 1 | provider | HTTP 429 |
| i-uc-cur-multibed | 2 | provider | HTTP 429 |

No failures at retrieval, temporal filtering, reranking, context assembly or citation.

Weakness recorded but with no effect on this suite's outcomes: reranking pushed the February Air Travel chunk out of the context in `i-uc-march-tickets`.

## Harness notes

* After run 2, I fixed a bug in the harness's own checks: it labelled wrong-organization UConn distractors in the version-neutral case `i-ru-advance` as a temporal failure. I recomputed the checks offline with `--recheck`, which makes no LLM calls; retrieval, temporal filtering and reranking were re-derived and matched the saved runs.
* No pipeline component was changed. No fixes were made in response to results.

## Test suite

`pytest`: **191 passed, 1 skipped.**
* The skip is the opt-in live generation suite (`RUN_LIVE_LLM=1`).
* The count includes 3 new offline tests in `tests/test_integration_suite.py`, which check that every case quote resolves to exactly one chunk.
* All earlier tests still pass.
