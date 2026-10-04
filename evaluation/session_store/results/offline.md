# Phase 5 offline evaluation (no language model)

The phase 3 conversations through the phase 4 job workflow on a session store, compared with the committed phase 3 evaluation (evaluation/session/results/offline.json) and the phase 3 controller called directly. memory = one worker on an InMemorySessionStore; restarted = a new worker and a new RedisSessionStore for every turn over an in-process stand-in for the Redis client (each turn starts from the encoded session). Real retrieval stack, stub model; see evaluation/session_store/run.py.

## 17/17 turns pass; 8/8 conversations with identical model input; 8/8 stored sessions equal to the direct one

| conversation | turn | question | resolution | memory | restarted | checks |
|---|---|---|---|---|---|---|
| s-direct | 1 | What are Rutgers' rules for travel advances? | self_contained | ✓ | ✓ | ok |
| s-direct | 2 | What documentation is required after the trip? | follow_up | ✓ | ✓ | ok |
| s-reference | 1 | What is UConn's University Travel Card suspension rule? | self_contained | ✓ | ✓ | ok |
| s-reference | 2 | What happens if they are still unresolved after 90 days? | follow_up | ✓ | ✓ | ok |
| s-temporal | 1 | What did UConn's February 2026 procedures say about when a University Travel Card is suspended? | self_contained | ✓ | ✓ | ok |
| s-temporal | 2 | Does that still apply under the current procedures? | follow_up | ✓ | ✓ | ok |
| s-chain | 1 | What is UConn's University Travel Card suspension rule? | self_contained | ✓ | ✓ | ok |
| s-chain | 2 | Does that apply currently? | follow_up | ✓ | ✓ | ok |
| s-chain | 3 | What happens after 90 days? | follow_up | ✓ | ✓ | ok |
| s-constraint | 1 | At Oregon State, what procurement method applies to a $180,000 purchase? | self_contained | ✓ | ✓ | ok |
| s-constraint | 2 | What if it is an emergency costing less than $2 million? | follow_up | ✓ | ✓ | ok |
| s-unrelated | 1 | What is UConn's University Travel Card suspension rule? | self_contained | ✓ | ✓ | ok |
| s-unrelated | 2 | How often must UT Austin user accounts be reviewed? | self_contained | ✓ | ✓ | ok |
| s-ambiguous | 1 | How often must UT Austin user accounts be reviewed, and is there a limit on the gratuities Rutgers will reimburse for business travel? | self_contained | ✓ | ✓ | ok |
| s-ambiguous | 2 | Does that apply currently? | unresolved | ✓ | ✓ | ok |
| s-unsupported | 1 | What are Rutgers' rules for travel advances? | self_contained | ✓ | ✓ | ok |
| s-unsupported | 2 | Does Harvard University have the same rule? | unresolved | ✓ | ✓ | ok |

✓ = completed job, turn index, resolution/query/strategy/versions/context equal to phase 3, answer and citations equal to the direct run.

| conversation | model calls | identical model input | stored = direct (memory / restarted) | encoded session |
|---|---|---|---|---|
| s-direct | 2 | True | True / True | 10,817 bytes |
| s-reference | 2 | True | True / True | 12,208 bytes |
| s-temporal | 2 | True | True / True | 12,265 bytes |
| s-chain | 3 | True | True / True | 17,917 bytes |
| s-constraint | 2 | True | True / True | 11,768 bytes |
| s-unrelated | 2 | True | True / True | 11,097 bytes |
| s-ambiguous | 1 | True | True / True | 7,818 bytes |
| s-unsupported | 1 | True | True / True | 5,632 bytes |
