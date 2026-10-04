# Phase 4 offline evaluation (no language model)

The phase 3 conversations (evaluation/session/cases.py) submitted as jobs through the in-memory broker to a worker running the phase 3 session controller, compared with calling that controller directly. Real retrieval stack, stub model; see evaluation/jobs/run.py.

## 17/17 turns pass; 8/8 conversations with byte-identical model input

| conversation | turn | question | job states | resolution | = direct resolution | = direct answer | checks |
|---|---|---|---|---|---|---|---|
| s-direct | 1 | What are Rutgers' rules for travel advances? | queued → processing → completed | self_contained | True | True | ok |
| s-direct | 2 | What documentation is required after the trip? | queued → processing → completed | follow_up | True | True | ok |
| s-reference | 1 | What is UConn's University Travel Card suspension rule? | queued → processing → completed | self_contained | True | True | ok |
| s-reference | 2 | What happens if they are still unresolved after 90 days? | queued → processing → completed | follow_up | True | True | ok |
| s-temporal | 1 | What did UConn's February 2026 procedures say about when a University Travel Card is suspended? | queued → processing → completed | self_contained | True | True | ok |
| s-temporal | 2 | Does that still apply under the current procedures? | queued → processing → completed | follow_up | True | True | ok |
| s-chain | 1 | What is UConn's University Travel Card suspension rule? | queued → processing → completed | self_contained | True | True | ok |
| s-chain | 2 | Does that apply currently? | queued → processing → completed | follow_up | True | True | ok |
| s-chain | 3 | What happens after 90 days? | queued → processing → completed | follow_up | True | True | ok |
| s-constraint | 1 | At Oregon State, what procurement method applies to a $180,000 purchase? | queued → processing → completed | self_contained | True | True | ok |
| s-constraint | 2 | What if it is an emergency costing less than $2 million? | queued → processing → completed | follow_up | True | True | ok |
| s-unrelated | 1 | What is UConn's University Travel Card suspension rule? | queued → processing → completed | self_contained | True | True | ok |
| s-unrelated | 2 | How often must UT Austin user accounts be reviewed? | queued → processing → completed | self_contained | True | True | ok |
| s-ambiguous | 1 | How often must UT Austin user accounts be reviewed, and is there a limit on the gratuities Rutgers will reimburse for business travel? | queued → processing → completed | self_contained | True | True | ok |
| s-ambiguous | 2 | Does that apply currently? | queued → processing → completed | unresolved | True | True | ok |
| s-unsupported | 1 | What are Rutgers' rules for travel advances? | queued → processing → completed | self_contained | True | True | ok |
| s-unsupported | 2 | Does Harvard University have the same rule? | queued → processing → completed | unresolved | True | True | ok |

| conversation | model calls | identical model input | broker rejections |
|---|---|---|---|
| s-direct | 2 | True | 0 |
| s-reference | 2 | True | 0 |
| s-temporal | 2 | True | 0 |
| s-chain | 3 | True | 0 |
| s-constraint | 2 | True | 0 |
| s-unrelated | 2 | True | 0 |
| s-ambiguous | 1 | True | 0 |
| s-unsupported | 1 | True | 0 |
