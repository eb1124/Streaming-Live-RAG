# Phase 2 offline evaluation (no language model)

Real retrieval stack (hybrid → temporal → rerank), stub model; see evaluation/multi_intent/run.py. "gold P1/P2" = each intent's gold evidence in the context phase 1 / phase 2 shows the model.

## Phase 2 cases: 16/16 pass the offline checks

| case | multi | strategy | calls P1→P2 | identical input | intents: covered / admissible (best score) / gold P1→P2 | checks |
|---|---|---|---|---|---|---|
| mi-uc-card | True | delegate | 1→1 | True | 1: cov / adm (4.0) / ✓→✓<br>2: cov / adm (7.6) / ✓→✓ | ok |
| mi-penn | True | delegate | 1→1 | True | 1: cov / adm (3.8) / ✓→✓<br>2: cov / adm (7.8) / ✓→✓ | ok |
| mi-mi-air | True | delegate | 1→1 | True | 1: cov / adm (7.3) / ✓→✓<br>2: cov / adm (8.5) / ✓→✓ | ok |
| mi-or-emergency | True | delegate | 1→1 | True | 1: cov / adm (4.8) / ✓→✓<br>2: cov / adm (0.9) / ✓→✓ | ok |
| mi-ut-ru | True | fused | 0→1 | False | 1: NEW / adm (4.4) / ✓→✓<br>2: cov / adm (4.7) / ✓→✓ | ok |
| mi-st-ro | True | fused | 1→1 | False | 1: cov / adm (8.6) / ✓→✓<br>2: NEW / adm (8.2) / ✗→✓ | ok |
| mi-mi-ru-dated | True | per_intent | 1→2 | False | 1: cov / adm (10.1) / ✓→✓<br>2: NEW / adm (5.7) / ✗→✓ | ok |
| mi-three | True | per_intent | 1→3 | False | 1: NEW / adm (4.4) / ✗→✓<br>2: cov / adm (10.1) / ✓→✓<br>3: NEW / adm (5.4) / ✗→✓ | ok |
| mi-uc-mixed-temporal | True | per_intent | 1→2 | False | 1: cov / adm (4.2) / ✓→✓<br>2: NEW / adm (6.7) / ✗→✓ | ok |
| mi-uc-jul-scope | True | delegate | 1→1 | True | 1: cov / adm (5.1) / ✓→✓<br>2: cov / adm (3.2) / ✓→✓ | ok |
| mi-ro-harvard | True | fused | 1→1 | False | 1: cov / adm (8.2) / ✓→✓<br>2: NEW / OUTSIDE CORPUS: Harvard University (3.5) / –→– | ok |
| mi-ru-yale | True | fused | 1→1 | False | 1: cov / adm (5.7) / ✓→✓<br>2: NEW / adm (1.9) / –→– | ok |
| mi-ctl-single | False | delegate | 1→1 | True | — | ok |
| mi-ctl-explain | False | delegate | 1→1 | True | — | ok |
| mi-ctl-refers | False | delegate | 1→1 | True | — | ok |
| mi-ctl-compare | False | delegate | 1→1 | True | — | ok |

## The 27 integration questions (read only): 2 decomposed, 27 delegate to phase 1, 27/27 with model input identical to phase 1

| question | multi | strategy | identical input | reason |
|---|---|---|---|---|
| i-uc-jul-submit | True | delegate | True | the question's own context already holds every admissible intent's best evidence: phase 1 unchanged |
| i-uc-personal-leg | True | delegate | True | the question's own context already holds every admissible intent's best evidence: phase 1 unchanged |

All other integration questions: single intent, delegate, identical model input.
