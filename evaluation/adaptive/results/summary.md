# Adaptive controller: automatic checks vs. the frozen baseline

Provider groq, model openai/gpt-oss-20b, prompt grounded-qa-2. 27 cases × 2 runs (evaluation/integration/cases.py). Checks: evaluation.integration.run.check (automatic only; not a verdict on answer correctness). Baseline: evaluation/integration/results/ (tag rag-baseline-2026-09-28).

## Run 1: 20/27 passed (baseline run 1: 23/27); split_by_version: 1 (i-uc-neutral-card)

| case | expect | got | strategy | LLM calls | baseline | adaptive | gold ranks | failures | decision reason |
|---|---|---|---|---|---|---|---|---|---|
| i-uc-jul-submit | answer | answer | single | 1 | pass | pass | 1→1→1 |  | question selects versions itself (intent point_in_time) |
| i-uc-feb-suspend | answer | answer | single | 1 | pass | pass | 2→1→1 |  | question selects versions itself (intent point_in_time) |
| i-uc-jul-owner | answer | answer | single | 1 | fail | fail | 1→1→1 | generation: explicit answer check: missing ['June 17, 2026'] / misstatement [] | question selects versions itself (intent point_in_time) |
| i-uc-cur-reinstate | answer | answer | single | 1 | pass | pass | 1→1→1 |  | question selects versions itself (intent current) |
| i-uc-cur-multibed | answer | answer | single | 1 | fail | fail | 1→1→1 | generation: explicit answer check: missing ['personal credit card'] / misstatement [] | question selects versions itself (intent current) |
| i-uc-cmp-card | answer | answer | single | 1 | pass | fail | 5→5→4; 4→4→5 | generation: explicit answer check: missing ['\\b60 days'] / misstatement [] | question selects versions itself (intent compare) |
| i-uc-neutral-card | answer | answer | split_by_version | 3 | fail | pass | 2→2→2; 1→1→1 |  | neutral question; versions' evidence differs; the single answer was rejected only for version attribution |
| i-uc-cmp-lodging | answer | answer | single | 1 | pass | pass | 3→3→3 |  | question selects versions itself (intent compare) |
| i-uc-march-tickets | abstain | abstain (model_insufficient_evidence) | single | 1 | pass | pass | 6→4→8✗ctx |  | question selects versions itself (intent point_in_time) |
| i-uc-feb-reinstate | abstain | abstain (model_insufficient_evidence) | single | 1 | pass | pass | 4→2→1 |  | question selects versions itself (intent point_in_time) |
| i-uc-unavailable | abstain | abstain (model_insufficient_evidence) | single | 1 | pass | pass | — |  | question selects versions itself (intent point_in_time) |
| i-uc-neu-sio | answer | answer | single | 1 | pass | pass | 3→3→1 |  | eligible (neutral question, versions' evidence differs) but the single answer was accepted |
| i-uc-spouse | answer | abstain (ungrounded_output) | single | 1 | pass | fail | 1→1→1; 2→2→2 | verification: abstained (ungrounded_output): claim 1: quote not found in cited source(s): '"A detailed justification specifying the official role of the accompanying indiv'; claim 1: quote not found in cited source(s): '"The traveler must submit | eligible (neutral question, versions' evidence differs) but the single answer was not rejected for version attribution alone (ungrounded_output) |
| i-uc-personal-leg | answer | answer | single | 1 | pass | pass | 2→2→2 |  | eligible (neutral question, versions' evidence differs) but the single answer was accepted |
| i-ru-tips | answer | answer | single | 1 | pass | fail | 1→1→1 | citation: {"wrong_org_cited": ["University of Connecticut"]} | no document has two or more versions in the context |
| i-ru-advance | answer | answer | single | 1 | pass | pass | 1→1→3 |  | versions in the context have identical evidence (same content hashes) |
| i-or-180k | answer | answer | single | 1 | pass | pass | 2→2→1; 3→3→6 |  | no document has two or more versions in the context |
| i-st-invoice | answer | answer | single | 1 | fail | fail | 1→1→1 | generation: explicit answer check: missing ['standard purchase order'] / misstatement [] | no document has two or more versions in the context |
| i-pe-exception | answer | abstain (ungrounded_output) | single | 1 | pass | fail | 1→1→1 | verification: abstained (ungrounded_output): claim 1: quote not found in cited source(s): '"Competitive bids are not required when purchasing goods or services from a Pref'; claim 1: quote not found in cited source(s): '"Preferred Contract Suppl | no document has two or more versions in the context |
| i-ut-clause | answer | answer | single | 1 | pass | pass | 1→1→2 |  | no document has two or more versions in the context |
| i-mi-mileage | answer | answer | single | 1 | pass | pass | 1→1→1 |  | question selects versions itself (intent point_in_time) |
| i-ro-highrisk | answer | answer | single | 1 | pass | pass | 1→1→1 |  | no document has two or more versions in the context |
| i-ya-internet | abstain | abstain (model_insufficient_evidence) | single | 1 | pass | pass | — |  | no document has two or more versions in the context |
| i-st-mileage | abstain | abstain (model_insufficient_evidence) | single | 1 | pass | pass | — |  | no document has two or more versions in the context |
| i-harvard-bids | abstain | abstain (model_insufficient_evidence) | single | 1 | pass | pass | — |  | no document has two or more versions in the context |
| i-ro-tips | abstain | abstain (model_insufficient_evidence) | single | 1 | pass | pass | — |  | no document has two or more versions in the context |
| i-uc-nyc-hotel | abstain | abstain (low_relevance) | single | 0 | pass | pass | 6→6→6 |  | eligible (neutral question, versions' evidence differs) but the single answer was not rejected for version attribution alone (low_relevance) |

LLM calls: 28 (errors: 0); tokens total 81107.

## Run 2: 22/27 passed (baseline run 2: 21/27); split_by_version: 1 (i-uc-neutral-card)

| case | expect | got | strategy | LLM calls | baseline | adaptive | gold ranks | failures | decision reason |
|---|---|---|---|---|---|---|---|---|---|
| i-uc-jul-submit | answer | answer | single | 1 | pass | pass | 1→1→1 |  | question selects versions itself (intent point_in_time) |
| i-uc-feb-suspend | answer | answer | single | 1 | pass | pass | 2→1→1 |  | question selects versions itself (intent point_in_time) |
| i-uc-jul-owner | answer | answer | single | 1 | fail | fail | 1→1→1 | generation: explicit answer check: missing ['June 17, 2026'] / misstatement [] | question selects versions itself (intent point_in_time) |
| i-uc-cur-reinstate | answer | answer | single | 1 | pass | pass | 1→1→1 |  | question selects versions itself (intent current) |
| i-uc-cur-multibed | answer | answer | single | 1 | fail | fail | 1→1→1 | generation: explicit answer check: missing ['personal credit card'] / misstatement [] | question selects versions itself (intent current) |
| i-uc-cmp-card | answer | answer | single | 1 | fail | pass | 5→5→4; 4→4→5 |  | question selects versions itself (intent compare) |
| i-uc-neutral-card | answer | answer | split_by_version | 3 | fail | pass | 2→2→2; 1→1→1 |  | neutral question; versions' evidence differs; the single answer was rejected only for version attribution |
| i-uc-cmp-lodging | answer | answer | single | 1 | pass | pass | 3→3→3 |  | question selects versions itself (intent compare) |
| i-uc-march-tickets | abstain | abstain (model_insufficient_evidence) | single | 1 | pass | pass | 6→4→8✗ctx |  | question selects versions itself (intent point_in_time) |
| i-uc-feb-reinstate | abstain | abstain (model_insufficient_evidence) | single | 1 | pass | pass | 4→2→1 |  | question selects versions itself (intent point_in_time) |
| i-uc-unavailable | abstain | abstain (model_insufficient_evidence) | single | 1 | pass | pass | — |  | question selects versions itself (intent point_in_time) |
| i-uc-neu-sio | answer | answer | single | 1 | pass | pass | 3→3→1 |  | eligible (neutral question, versions' evidence differs) but the single answer was accepted |
| i-uc-spouse | answer | abstain (ungrounded_output) | single | 1 | pass | fail | 1→1→1; 2→2→2 | verification: abstained (ungrounded_output): claim 1: quote not found in cited source(s): '"A detailed justification specifying the official role of the accompanying indiv'; claim 1: quote not found in cited source(s): '"The traveler must submit | eligible (neutral question, versions' evidence differs) but the single answer was not rejected for version attribution alone (ungrounded_output) |
| i-uc-personal-leg | answer | answer | single | 1 | fail | pass | 2→2→2 |  | eligible (neutral question, versions' evidence differs) but the single answer was accepted |
| i-ru-tips | answer | answer | single | 1 | pass | pass | 1→1→1 |  | no document has two or more versions in the context |
| i-ru-advance | answer | answer | single | 1 | pass | pass | 1→1→3 |  | versions in the context have identical evidence (same content hashes) |
| i-or-180k | answer | answer | single | 1 | pass | pass | 2→2→1; 3→3→6 |  | no document has two or more versions in the context |
| i-st-invoice | answer | answer | single | 1 | pass | fail | 1→1→1 | generation: explicit answer check: missing ['standard purchase order'] / misstatement [] | no document has two or more versions in the context |
| i-pe-exception | answer | abstain (ungrounded_output) | single | 1 | fail | fail | 1→1→1 | verification: abstained (ungrounded_output): claim 1: quote not found in cited source(s): '"Competitive bids are not required when purchasing goods or services from a Pref'; claim 1: quote not found in cited source(s): '"Preferred Contract Suppl | no document has two or more versions in the context |
| i-ut-clause | answer | answer | single | 1 | pass | pass | 1→1→2 |  | no document has two or more versions in the context |
| i-mi-mileage | answer | answer | single | 1 | pass | pass | 1→1→1 |  | question selects versions itself (intent point_in_time) |
| i-ro-highrisk | answer | answer | single | 1 | pass | pass | 1→1→1 |  | no document has two or more versions in the context |
| i-ya-internet | abstain | abstain (model_insufficient_evidence) | single | 1 | pass | pass | — |  | no document has two or more versions in the context |
| i-st-mileage | abstain | abstain (model_insufficient_evidence) | single | 1 | pass | pass | — |  | no document has two or more versions in the context |
| i-harvard-bids | abstain | abstain (model_insufficient_evidence) | single | 1 | pass | pass | — |  | no document has two or more versions in the context |
| i-ro-tips | abstain | abstain (model_insufficient_evidence) | single | 1 | pass | pass | — |  | no document has two or more versions in the context |
| i-uc-nyc-hotel | abstain | abstain (low_relevance) | single | 0 | pass | pass | 6→6→6 |  | eligible (neutral question, versions' evidence differs) but the single answer was not rejected for version attribution alone (low_relevance) |

LLM calls: 28 (errors: 0); tokens total 81405.

## Run-to-run agreement

same_retrieval: 27/27 · same_temporal: 27/27 · same_rerank: 27/27 · same_context: 27/27 · same_status: 27/27 · same_abstention_reason: 27/27 · same_citations: 24/27 · same_explicit_check: 26/27 · identical_text: 19/27

