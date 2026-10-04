# Phase 3 offline evaluation (no language model)

Real retrieval stack (hybrid → temporal → rerank), stub model; see evaluation/session/run.py. "gold" = each gold unit shown to the model, with the session → for the question alone (phase 2 without the session); "= P2" = a self-contained turn's model input is byte-identical to phase 2 on the question alone.

## 17/17 turns pass the offline checks (8 conversations)

| conversation | turn | question | resolution | query phase 2 receives | temporal / versions | calls | gold | = P2 | checks |
|---|---|---|---|---|---|---|---|---|---|
| s-direct | 1 | What are Rutgers' rules for travel advances? | self_contained: first turn of the session | (verbatim) | neutral / – | 1 | ✓ | True | ok |
| s-direct | 2 | What documentation is required after the trip? | follow_up: names no organization | (Earlier in this conversation: What are Rutgers' rules for travel advances). What documentation is required after the trip? | neutral / – | 1 | ✓ → alone ✗ | – | ok |
| s-reference | 1 | What is UConn's University Travel Card suspension rule? | self_contained: first turn of the session | (verbatim) | neutral / – | 1 | ✓ | True | ok |
| s-reference | 2 | What happens if they are still unresolved after 90 days? | follow_up: names no organization | (Earlier in this conversation: What is UConn's University Travel Card suspension rule). What happens if they are still unresolved after 90 days? | neutral / – | 1 | ✓ → alone ✓ | – | ok |
| s-temporal | 1 | What did UConn's February 2026 procedures say about when a University Travel Card is suspended? | self_contained: first turn of the session | (verbatim) | point_in_time / travel-and-entertainment-procedures-final-ccccf9 | 1 | ✓ | True | ok |
| s-temporal | 2 | Does that still apply under the current procedures? | follow_up: refers back (Does that) | (Earlier in this conversation: What did UConn's procedures say about when a University Travel Card is suspended). Does that still apply under the current procedures? | current / 2026-07-01-travel-and-entertainment-procedures-ca903b | 1 | ✓ → alone ✗ | – | ok |
| s-chain | 1 | What is UConn's University Travel Card suspension rule? | self_contained: first turn of the session | (verbatim) | neutral / – | 1 | ✓ | True | ok |
| s-chain | 2 | Does that apply currently? | follow_up: refers back (Does that) | (Earlier in this conversation: What is UConn's University Travel Card suspension rule). Does that apply currently? | current / 2026-07-01-travel-and-entertainment-procedures-ca903b | 1 | ✓ → alone ✗ | – | ok |
| s-chain | 3 | What happens after 90 days? | follow_up: names no organization | (Earlier in this conversation: What is UConn's University Travel Card suspension rule; Does that apply; version: currently). What happens after 90 days? | current / 2026-07-01-travel-and-entertainment-procedures-ca903b | 1 | ✓ → alone ✓ | – | ok |
| s-constraint | 1 | At Oregon State, what procurement method applies to a $180,000 purchase? | self_contained: first turn of the session | (verbatim) | neutral / – | 1 | ✓ | True | ok |
| s-constraint | 2 | What if it is an emergency costing less than $2 million? | follow_up: refers back (What if) | (Earlier in this conversation: At Oregon State, what procurement method applies to a $180,000 purchase). What if it is an emergency costing less than $2 million? | neutral / – | 1 | ✓ → alone ✓ | – | ok |
| s-unrelated | 1 | What is UConn's University Travel Card suspension rule? | self_contained: first turn of the session | (verbatim) | neutral / – | 1 | ✓ | True | ok |
| s-unrelated | 2 | How often must UT Austin user accounts be reviewed? | self_contained: names its own organization and does not refer back | (verbatim) | neutral / – | 1 | ✓ | True | ok |
| s-ambiguous | 1 | How often must UT Austin user accounts be reviewed, and is there a limit on the gratuities Rutgers will reimburse for business travel? | self_contained: first turn of the session | (verbatim) | neutral / – | 1 | – | True | ok |
| s-ambiguous | 2 | Does that apply currently? | unresolved: refers back (Does that), but the previous turn asked 2 questions, so the reference is ambiguous | — (no retrieval) | – / – | 0 | – | – | ok |
| s-unsupported | 1 | What are Rutgers' rules for travel advances? | self_contained: first turn of the session | (verbatim) | neutral / – | 1 | ✓ | True | ok |
| s-unsupported | 2 | Does Harvard University have the same rule? | unresolved: refers back (the same rule), but it names Harvard University while the topic is about Rutgers University | — (no retrieval) | – / – | 0 | – | – | ok |

Follow-ups with gold evidence: 6/6 show it with the session, 3/6 when phase 2 gets the follow-up alone.
