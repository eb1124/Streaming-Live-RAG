# Phase 2 live evaluation

Phase 2 = MultiIntentController; phase 1 = AdaptiveController, run separately only when phase 2 did not delegate (a delegated answer is phase 1's own). Checks are automatic (evaluation/multi_intent/live.py), not a verdict on correctness.

## Run 1 (complete): 14/16 done cases pass the checks, 16/16 done

LLM calls: phase 2 22, phase 1 comparison 6; tokens 86848; provider errors: 0

| case | strategy | P2 status | intents | P2 calls | P1 (run: status, calls) | tokens | checks |
|---|---|---|---|---|---|---|---|
| mi-uc-card | delegate | answered | 1:answered, 2:answered | 3 | shared: answered, 0 | 9314 | ok |
| mi-penn | delegate | answered | 1:answered, 2:answered | 1 | shared: answered, 0 | 3053 | ok |
| mi-mi-air | delegate | answered | 1:answered, 2:answered | 1 | shared: answered, 0 | 2587 | ok |
| mi-or-emergency | delegate | abstained | 1:not_answered, 2:not_answered | 1 | shared: abstained, 0 | 4391 | intent 1: expected an answer, got not_answered; intent 2: expected an answer, got not_answered |
| mi-ut-ru | fused | answered | 1:answered, 2:answered | 1 | yes: abstained, 0 | 3408 | ok |
| mi-st-ro | fused | answered | 1:answered, 2:answered | 1 | yes: answered, 1 | 5824 | ok |
| mi-mi-ru-dated | per_intent | answered | 1:answered, 2:answered | 2 | yes: answered, 1 | 9216 | ok |
| mi-three | per_intent | answered | 1:answered, 2:answered, 3:answered | 3 | yes: answered, 1 | 11627 | ok |
| mi-uc-mixed-temporal | per_intent | answered | 1:answered, 2:answered | 2 | yes: answered, 1 | 9127 | intent 2: missing facts ['personal credit card'] |
| mi-uc-jul-scope | delegate | answered | 1:answered, 2:answered | 1 | shared: answered, 0 | 2821 | ok |
| mi-ro-harvard | fused | answered | 1:answered, 2:unsupported | 1 | yes: answered, 1 | 6182 | ok |
| mi-ru-yale | fused | answered | 1:answered, 2:not_answered | 1 | yes: answered, 1 | 6014 | ok |
| mi-ctl-single | delegate | abstained | — | 1 | shared: abstained, 0 | 2565 | ok |
| mi-ctl-explain | delegate | abstained | — | 1 | shared: abstained, 0 | 3026 | ok |
| mi-ctl-refers | delegate | answered | — | 1 | shared: answered, 0 | 3328 | ok |
| mi-ctl-compare | delegate | abstained | — | 1 | shared: abstained, 0 | 4365 | ok |
