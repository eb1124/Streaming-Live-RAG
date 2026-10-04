# Integration suite: automatic checks

Provider groq, model openai/gpt-oss-20b, prompt grounded-qa-2, settings {'temperature': 0.0, 'seed': 1234, 'reasoning_effort': 'medium', 'max_completion_tokens': 4096}. 27 cases × 2 runs. Automatic checks only (retrieval ranks, temporal selection, citations, status, regex fact checks). They are not a verdict on answer correctness; see report.md for the manual review.

Gold column: per evidence unit, hybrid rank → rank in the post-temporal rerank pool → rerank rank (✗ctx = not in context).

## Run 1: 23/27 passed all automatic checks

| case | expect | got | intent / selected | gold ranks | cited | primary failing stage | failures |
|---|---|---|---|---|---|---|---|
| i-uc-jul-submit | answer | answer | point_in_time / jul | 1→1→1 | 2026-07-01-tra…bc7d99 |  |  |
| i-uc-feb-suspend | answer | answer | point_in_time / feb | 2→1→1 | travel-and-ent…113880 |  |  |
| i-uc-jul-owner | answer | answer | point_in_time / jul | 1→1→1 | 2026-07-01-tra…ad0b00 | generation | generation: explicit answer check: missing ['June 17, 2026'] / misstatement [] |
| i-uc-cur-reinstate | answer | answer | current / jul | 1→1→1 | 2026-07-01-tra…bc7d99 |  |  |
| i-uc-cur-multibed | answer | answer | current / jul | 1→1→1 | 2026-07-01-tra…230e93 | generation | generation: explicit answer check: missing ['personal credit card'] / misstatement [] |
| i-uc-cmp-card | answer | answer | compare / jul,feb | 5→5→4; 4→4→5 | travel-and-ent…113880, 2026-07-01-tra…bc7d99 |  |  |
| i-uc-neutral-card | answer | abstain (ungrounded_output) | neutral / — | 2→2→2; 1→1→1 | — | verification | verification: abstained (ungrounded_output): claim 1: merges versions: quote 'If University Issued Travel Card charges are not submitted a' is not in the version effective 2026-02-01 of Travel and Entertainment Procedures; state each version's r |
| i-uc-cmp-lodging | answer | answer | compare / jul,feb | 3→3→3 | 2026-07-01-tra…230e93 |  |  |
| i-uc-march-tickets | abstain | abstain (model_insufficient_evidence) | point_in_time / feb | 6→4→8✗ctx | — |  |  |
| i-uc-feb-reinstate | abstain | abstain (model_insufficient_evidence) | point_in_time / feb | 4→2→1 | — |  |  |
| i-uc-unavailable | abstain | abstain (model_insufficient_evidence) | point_in_time / none | — | — |  |  |
| i-uc-neu-sio | answer | answer | neutral / — | 3→3→1 | 2026-07-01-tra…2cdaba |  |  |
| i-uc-spouse | answer | answer | neutral / — | 1→1→1; 2→2→2 | travel-and-ent…986e06, 2026-07-01-tra…f439d4, travel-and-ent…5bbb86 |  |  |
| i-uc-personal-leg | answer | answer | neutral / — | 2→2→2 | 2026-07-01-tra…b78821, travel-and-ent…6d97bd, travel-and-ent…5d99ac |  |  |
| i-ru-tips | answer | answer | neutral / — | 1→1→1 | chapter-11-uni…a70791 |  |  |
| i-ru-advance | answer | answer | neutral / — | 1→1→3 | chapter-11-uni…a77ccd |  |  |
| i-or-180k | answer | answer | neutral / — | 2→2→1; 3→3→6 | 03-010-procure…2956d2, 03-010-procure…61e498 |  |  |
| i-st-invoice | answer | answer | neutral / — | 1→1→1 | 5-1-1-procurem…ca9cba | generation | generation: explicit answer check: missing ['standard purchase order'] / misstatement [] |
| i-pe-exception | answer | answer | neutral / — | 1→1→1 | 2305-complianc…d09b2b |  |  |
| i-ut-clause | answer | answer | neutral / — | 1→1→2 | information-re…23d33a |  |  |
| i-mi-mileage | answer | answer | point_in_time / jul | 1→1→1 | travel-booking…a8d2df |  |  |
| i-ro-highrisk | answer | answer | neutral / — | 1→1→1 | international-…6f9066 |  |  |
| i-ya-internet | abstain | abstain (model_insufficient_evidence) | neutral / — | — | — |  |  |
| i-st-mileage | abstain | abstain (model_insufficient_evidence) | neutral / — | — | — |  |  |
| i-harvard-bids | abstain | abstain (model_insufficient_evidence) | neutral / — | — | — |  |  |
| i-ro-tips | abstain | abstain (model_insufficient_evidence) | neutral / — | — | — |  |  |
| i-uc-nyc-hotel | abstain | abstain (low_relevance) | neutral / — | 6→6→6 | — |  |  |

LLM calls: 26 (errors: 0); tokens: prompt 59561, completion 15594, total 75155; end-to-end latency excl. pacing: total 460.6 s.

## Run 2: 21/27 passed all automatic checks

| case | expect | got | intent / selected | gold ranks | cited | primary failing stage | failures |
|---|---|---|---|---|---|---|---|
| i-uc-jul-submit | answer | answer | point_in_time / jul | 1→1→1 | 2026-07-01-tra…bc7d99 |  |  |
| i-uc-feb-suspend | answer | answer | point_in_time / feb | 2→1→1 | travel-and-ent…113880 |  |  |
| i-uc-jul-owner | answer | answer | point_in_time / jul | 1→1→1 | 2026-07-01-tra…ad0b00 | generation | generation: explicit answer check: missing ['June 17, 2026'] / misstatement [] |
| i-uc-cur-reinstate | answer | answer | current / jul | 1→1→1 | 2026-07-01-tra…bc7d99 |  |  |
| i-uc-cur-multibed | answer | answer | current / jul | 1→1→1 | 2026-07-01-tra…230e93 | generation | generation: explicit answer check: missing ['personal credit card'] / misstatement [] |
| i-uc-cmp-card | answer | answer | compare / jul,feb | 5→5→4; 4→4→5 | travel-and-ent…113880, 2026-07-01-tra…bc7d99 | generation | generation: explicit answer check: missing ['\\b60 days'] / misstatement [] |
| i-uc-neutral-card | answer | abstain (ungrounded_output) | neutral / — | 2→2→2; 1→1→1 | — | verification | verification: abstained (ungrounded_output): claim 1: merges versions: quote 'If University Issued Travel Card charges are not submitted a' is not in the version effective 2026-02-01 of Travel and Entertainment Procedures; state each version's r |
| i-uc-cmp-lodging | answer | answer | compare / jul,feb | 3→3→3 | 2026-07-01-tra…230e93 |  |  |
| i-uc-march-tickets | abstain | abstain (model_insufficient_evidence) | point_in_time / feb | 6→4→8✗ctx | — |  |  |
| i-uc-feb-reinstate | abstain | abstain (model_insufficient_evidence) | point_in_time / feb | 4→2→1 | — |  |  |
| i-uc-unavailable | abstain | abstain (model_insufficient_evidence) | point_in_time / none | — | — |  |  |
| i-uc-neu-sio | answer | answer | neutral / — | 3→3→1 | 2026-07-01-tra…2cdaba, travel-and-ent…aea901 |  |  |
| i-uc-spouse | answer | answer | neutral / — | 1→1→1; 2→2→2 | travel-and-ent…986e06, 2026-07-01-tra…f439d4, travel-and-ent…5bbb86 |  |  |
| i-uc-personal-leg | answer | abstain (ungrounded_output) | neutral / — | 2→2→2 | — | verification | verification: abstained (ungrounded_output): claim 1: quote not found in cited source(s): '"If a personal leg or segment is added, the University will not reimburse the ad'; claim 1: cites the version effective 2026-07-01 of Travel and Entertain |
| i-ru-tips | answer | answer | neutral / — | 1→1→1 | chapter-11-uni…a70791 |  |  |
| i-ru-advance | answer | answer | neutral / — | 1→1→3 | chapter-11-uni…a77ccd |  |  |
| i-or-180k | answer | answer | neutral / — | 2→2→1; 3→3→6 | 03-010-procure…2956d2, 03-010-procure…61e498 |  |  |
| i-st-invoice | answer | answer | neutral / — | 1→1→1 | 5-1-1-procurem…ca9cba |  |  |
| i-pe-exception | answer | answer | neutral / — | 1→1→1 | 2305-complianc…d09b2b | generation | generation: explicit answer check: missing ['not required'] / misstatement [] |
| i-ut-clause | answer | answer | neutral / — | 1→1→2 | information-re…23d33a |  |  |
| i-mi-mileage | answer | answer | point_in_time / jul | 1→1→1 | travel-booking…a8d2df |  |  |
| i-ro-highrisk | answer | answer | neutral / — | 1→1→1 | international-…6f9066 |  |  |
| i-ya-internet | abstain | abstain (model_insufficient_evidence) | neutral / — | — | — |  |  |
| i-st-mileage | abstain | abstain (model_insufficient_evidence) | neutral / — | — | — |  |  |
| i-harvard-bids | abstain | abstain (model_insufficient_evidence) | neutral / — | — | — |  |  |
| i-ro-tips | abstain | abstain (model_insufficient_evidence) | neutral / — | — | — |  |  |
| i-uc-nyc-hotel | abstain | abstain (low_relevance) | neutral / — | 6→6→6 | — |  |  |

LLM calls: 26 (errors: 0); tokens: prompt 59561, completion 16739, total 76300; end-to-end latency excl. pacing: total 470.3 s.

## Run-to-run agreement

same_retrieval: 27/27 · same_temporal: 27/27 · same_rerank: 27/27 · same_context: 27/27 · same_status: 26/27 · same_abstention_reason: 26/27 · same_citations: 25/27 · same_explicit_check: 24/27 · identical_text: 17/27

