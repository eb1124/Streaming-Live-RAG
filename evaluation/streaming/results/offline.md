# Phase 6 offline evaluation (no language model)

Baseline = the frozen retrieval (generation.pipeline.retrieve); streaming = bounded iterative retrieval (adaptive/streaming, at most 3 rounds). Real retrieval stack, stub model; see evaluation/streaming/run.py. Answers are not evaluated offline: an identical model input means the baseline's answer; a changed input needs a live run.

## Summary: 0 check failures

* Integration questions: 24/27 stop after one round, 3 take more; still insufficient after the budget: i-st-mileage, i-ro-tips, i-uc-nyc-hotel; structurally unresolved: i-harvard-bids.
* Model input identical to the baseline for 27/27 integration questions, including 20/20 whose baseline answer passed every check in every committed live run.
* Phase 2 cases: 16/16 with identical model input; strategy changed for 0.
* Phase 3 turns: 17/17 with the same resolution, 17 with identical model input.

## Integration questions

| case | expect | baseline correct | rounds | stop | queries after the first | gold B→S | forbidden B→S | other-org sources B→S | gate B→S | = input | checks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| i-uc-jul-submit | answer | True | 1 | sufficient | – | ✓→✓ | 0→0 | 0→0 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-uc-feb-suspend | answer | True | 1 | sufficient | – | ✓→✓ | 0→0 | 0→0 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-uc-jul-owner | answer | False | 1 | sufficient | – | ✓→✓ | 0→0 | 0→0 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-uc-cur-reinstate | answer | True | 1 | sufficient | – | ✓→✓ | 0→0 | 1→1 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-uc-cur-multibed | answer | False | 1 | sufficient | – | ✓→✓ | 0→0 | 1→1 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-uc-cmp-card | answer | False | 1 | sufficient | – | ✓✓→✓✓ | 0→0 | 1→1 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-uc-neutral-card | answer | False | 1 | sufficient | – | ✓✓→✓✓ | 0→0 | 1→1 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-uc-cmp-lodging | answer | True | 1 | sufficient | – | ✓→✓ | 0→0 | 0→0 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-uc-march-tickets | abstain | True | 1 | sufficient | – | ✗→✗ | 0→0 | 6→6 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-uc-feb-reinstate | abstain | True | 1 | sufficient | – | ✓→✓ | 0→0 | 6→6 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-uc-unavailable | abstain | True | 1 | sufficient | – | –→– | 0→0 | 6→6 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-uc-neu-sio | answer | True | 1 | sufficient | – | ✓→✓ | 0→0 | 0→0 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-uc-spouse | answer | True | 1 | sufficient | – | ✓✓→✓✓ | 0→0 | 0→0 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-uc-personal-leg | answer | False | 1 | sufficient | – | ✓→✓ | 0→0 | 0→0 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-ru-tips | answer | True | 1 | sufficient | – | ✓→✓ | 0→0 | 1→1 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-ru-advance | answer | True | 1 | sufficient | – | ✓→✓ | 2→2 | 2→2 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-or-180k | answer | True | 1 | sufficient | – | ✓✓→✓✓ | 0→0 | 0→0 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-st-invoice | answer | False | 1 | sufficient | – | ✓→✓ | 0→0 | 0→0 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-pe-exception | answer | False | 1 | sufficient | – | ✓→✓ | 0→0 | 3→3 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-ut-clause | answer | True | 1 | sufficient | – | ✓→✓ | 0→0 | 0→0 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-mi-mileage | answer | True | 1 | sufficient | – | ✓→✓ | 1→1 | 1→1 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-ro-highrisk | answer | True | 1 | sufficient | – | ✓→✓ | 0→0 | 0→0 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-ya-internet | abstain | True | 1 | sufficient | – | –→– | 0→0 | 6→6 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-st-mileage | abstain | True | 3 | max_rounds | What mileage rate does Stanford University reimburse for personal car use (Stanford University)<br>mileage rate Stanford University reimburse personal car use | –→– | 0→0 | 6→6 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-harvard-bids | abstain | True | 1 | structurally_unresolved | – | –→– | 0→0 | 6→6 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-ro-tips | abstain | True | 3 | max_rounds | What is the maximum tip percentage the University of Rochester will reimburse (University of Rochester)<br>maximum tip percentage University Rochester reimburse | –→– | 0→0 | 6→6 | model_insufficient_evidence→model_insufficient_evidence | True | ok |
| i-uc-nyc-hotel | abstain | True | 3 | max_rounds | What is the maximum nightly hotel rate UConn will reimburse in New York City (University of Connecticut)<br>maximum nightly hotel rate UConn reimburse New York City | ✓→✓ | 0→0 | 6→6 | low_relevance→low_relevance | True | ok |

### Integration questions whose model input changed

None.

## Phase 6 cases (evaluation/streaming/cases.py: from the phase 2 manual review; not a blind test)

| case | rounds | stop | queries after the first | promoted | gold B→S | context organizations B→S | = input | checks |
|---|---|---|---|---|---|---|---|---|
| s6-uc-ru-scope | 2 | sufficient | At UConn, what is the University Travel Card suspension rule, and, what is the travel advance rule (University of Connecticut) | travel-and-entertainment-procedures-final-ccccf9::005-bb1bf7113880 | ✗✓→✓✓ | {'Rutgers University': 6} → {'Rutgers University': 5, 'University of Connecticut': 1} | False | ok |
| s6-harvard-penn-scope | 1 | sufficient | – | – | –→– | {'University of Pennsylvania': 3, 'Stanford University': 3} → {'University of Pennsylvania': 3, 'Stanford University': 3} | True | ok |

## Phase 2 cases

| case | retrievals | rounds per retrieval | strategy B→S | calls B→S | gold per intent B→S | = input | checks |
|---|---|---|---|---|---|---|---|
| mi-uc-card | 3 | [1, 1, 1] | delegate→delegate | 1→1 | ✓→✓; ✓→✓ | True | ok |
| mi-penn | 3 | [1, 1, 1] | delegate→delegate | 1→1 | ✓→✓; ✓→✓ | True | ok |
| mi-mi-air | 3 | [1, 1, 1] | delegate→delegate | 1→1 | ✓→✓; ✓→✓ | True | ok |
| mi-or-emergency | 3 | [1, 1, 1] | delegate→delegate | 1→1 | ✓→✓; ✓→✓ | True | ok |
| mi-ut-ru | 3 | [3, 1, 1] | fused→fused | 1→1 | ✓→✓; ✓→✓ | True | ok |
| mi-st-ro | 3 | [3, 1, 1] | fused→fused | 1→1 | ✓→✓; ✓→✓ | True | ok |
| mi-mi-ru-dated | 3 | [1, 1, 1] | per_intent→per_intent | 2→2 | ✓→✓; ✓→✓ | True | ok |
| mi-three | 4 | [3, 1, 1, 1] | per_intent→per_intent | 3→3 | ✓→✓; ✓→✓; ✓→✓ | True | ok |
| mi-uc-mixed-temporal | 3 | [1, 1, 1] | per_intent→per_intent | 2→2 | ✓→✓; ✓→✓ | True | ok |
| mi-uc-jul-scope | 3 | [1, 1, 1] | delegate→delegate | 1→1 | ✓→✓; ✓→✓ | True | ok |
| mi-ro-harvard | 3 | [1, 1, 1] | fused→fused | 1→1 | ✓→✓; –→– | True | ok |
| mi-ru-yale | 3 | [3, 1, 1] | fused→fused | 1→1 | ✓→✓; –→– | True | ok |
| mi-ctl-single | 1 | [1] | delegate→delegate | 1→1 | – | True | ok |
| mi-ctl-explain | 1 | [1] | delegate→delegate | 1→1 | – | True | ok |
| mi-ctl-refers | 1 | [1] | delegate→delegate | 1→1 | – | True | ok |
| mi-ctl-compare | 1 | [1] | delegate→delegate | 1→1 | – | True | ok |

## Phase 3 conversations

| conversation | turn | resolution B / S | rounds per retrieval | gold B→S | = input | checks |
|---|---|---|---|---|---|---|
| s-direct | 1 | self_contained / self_contained | [1] | ✓→✓ | True | ok |
| s-direct | 2 | follow_up / follow_up | [1] | ✓→✓ | True | ok |
| s-reference | 1 | self_contained / self_contained | [1] | ✓→✓ | True | ok |
| s-reference | 2 | follow_up / follow_up | [1] | ✓→✓ | True | ok |
| s-temporal | 1 | self_contained / self_contained | [1] | ✓→✓ | True | ok |
| s-temporal | 2 | follow_up / follow_up | [1] | ✓→✓ | True | ok |
| s-chain | 1 | self_contained / self_contained | [1] | ✓→✓ | True | ok |
| s-chain | 2 | follow_up / follow_up | [1] | ✓→✓ | True | ok |
| s-chain | 3 | follow_up / follow_up | [1] | ✓→✓ | True | ok |
| s-constraint | 1 | self_contained / self_contained | [1] | ✓→✓ | True | ok |
| s-constraint | 2 | follow_up / follow_up | [1] | ✓→✓ | True | ok |
| s-unrelated | 1 | self_contained / self_contained | [1] | ✓→✓ | True | ok |
| s-unrelated | 2 | self_contained / self_contained | [1] | ✓→✓ | True | ok |
| s-ambiguous | 1 | self_contained / self_contained | [3, 1, 1] | –→– | True | ok |
| s-ambiguous | 2 | unresolved / unresolved | – | –→– | True | ok |
| s-unsupported | 1 | self_contained / self_contained | [1] | ✓→✓ | True | ok |
| s-unsupported | 2 | unresolved / unresolved | – | –→– | True | ok |
