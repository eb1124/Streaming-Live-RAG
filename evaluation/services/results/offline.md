# Phase 7 offline evaluation (no language model)

Queries through the services (api → phase 4 job → orchestrator → retrieval / generation services, all in one process: services.inprocess) compared with the direct pipeline (SessionController). Real retrieval stack, stub model; see evaluation/services/run.py.

## 69/69 turns equal to the direct pipeline; 42/42 conversations with identical model input; 42/42 stored sessions equal to the direct session

### phase2 (single retrieval): 16/16 turns

| conversation | turn | question | resolution | strategy | evidence | = input | = session | checks |
|---|---|---|---|---|---|---|---|---|
| mi-uc-card | 1 | What is the UConn University Travel Card submission deadline, and what happens if charges aren't submitted on time? | self_contained | delegate | 6 | True | True | ok |
| mi-penn | 1 | At Penn, what happens to purchase orders of $50,000 or more before issuance, and when are competitive bids not required? | self_contained | delegate | 6 | True | True | ok |
| mi-mi-air | 1 | What airfare does the University of Michigan pay for, and how far in advance does the University of Michigan recommend booking airfare? | self_contained | delegate | 6 | True | True | ok |
| mi-or-emergency | 1 | At Oregon State, what procurement method applies to a $180,000 purchase, and what happens to the required process if the procurement is an emergency costing less than $2 million? | self_contained | delegate | 6 | True | True | ok |
| mi-ut-ru | 1 | How often must UT Austin user accounts be reviewed, and is there a limit on the gratuities Rutgers will reimburse for business travel? | self_contained | fused | 6 | True | True | ok |
| mi-st-ro | 1 | Under what conditions can Stanford pay an invoice that exceeds the purchase order amount without a written change order, and how far ahead should a University of Rochester traveler submit a request for travel to a high-risk location? | self_contained | fused | 6 | True | True | ok |
| mi-mi-ru-dated | 1 | What mileage rate does the University of Michigan reimburse for business travel that starts on or after August 1, 2026, and how far in advance of departure must a Rutgers travel advance request be submitted? | self_contained | per_intent | 12 | True | True | ok |
| mi-three | 1 | How often must UT Austin user accounts be reviewed, what mileage rate does the University of Michigan reimburse for travel starting on or after August 1, 2026, and how far in advance must a Rutgers travel advance request be submitted? | self_contained | per_intent | 18 | True | True | ok |
| mi-uc-mixed-temporal | 1 | What did UConn's February 2026 procedures say about when a University Travel Card is suspended, and may a traveler currently pay for a multi-bedroom accommodation with a University Travel Card? | self_contained | per_intent | 12 | True | True | ok |
| mi-uc-jul-scope | 1 | Under UConn's procedures effective July 1, 2026, how is a suspended University Travel Card reinstated, and what approval is needed before a spouse's travel expenses can be reimbursed? | self_contained | delegate | 6 | True | True | ok |
| mi-ro-harvard | 1 | How far ahead should a University of Rochester traveler submit a request for travel to a high-risk location, and what purchase amount requires competitive bids at Harvard University? | self_contained | fused | 6 | True | True | ok |
| mi-ru-yale | 1 | How far in advance of departure must a Rutgers travel advance request be submitted, and does Yale reimburse home internet costs for staff who work remotely? | self_contained | fused | 6 | True | True | ok |
| mi-ctl-single | 1 | What does the University of Rochester policy say about tipping reimbursement? | self_contained | delegate | 6 | True | True | ok |
| mi-ctl-explain | 1 | At Stanford, can a $240 invoice overage on a $2,000 standard purchase order be paid without a written change order? Explain why. | self_contained | delegate | 6 | True | True | ok |
| mi-ctl-refers | 1 | Which clause of UT Austin's information resources policy sets how often user accounts must be reviewed, and how often is that? | self_contained | delegate | 6 | True | True | ok |
| mi-ctl-compare | 1 | Compare the February 1, 2026 and July 1, 2026 UConn Travel Card penalty rules. | self_contained | delegate | 6 | True | True | ok |

### phase3 (single retrieval): 17/17 turns

| conversation | turn | question | resolution | strategy | evidence | = input | = session | checks |
|---|---|---|---|---|---|---|---|---|
| s-direct | 1 | What are Rutgers' rules for travel advances? | self_contained | delegate | 6 | True | True | ok |
| s-direct | 2 | What documentation is required after the trip? | follow_up | delegate | 6 | True | True | ok |
| s-reference | 1 | What is UConn's University Travel Card suspension rule? | self_contained | delegate | 6 | True | True | ok |
| s-reference | 2 | What happens if they are still unresolved after 90 days? | follow_up | delegate | 6 | True | True | ok |
| s-temporal | 1 | What did UConn's February 2026 procedures say about when a University Travel Card is suspended? | self_contained | delegate | 6 | True | True | ok |
| s-temporal | 2 | Does that still apply under the current procedures? | follow_up | delegate | 6 | True | True | ok |
| s-chain | 1 | What is UConn's University Travel Card suspension rule? | self_contained | delegate | 6 | True | True | ok |
| s-chain | 2 | Does that apply currently? | follow_up | delegate | 6 | True | True | ok |
| s-chain | 3 | What happens after 90 days? | follow_up | delegate | 6 | True | True | ok |
| s-constraint | 1 | At Oregon State, what procurement method applies to a $180,000 purchase? | self_contained | delegate | 6 | True | True | ok |
| s-constraint | 2 | What if it is an emergency costing less than $2 million? | follow_up | delegate | 6 | True | True | ok |
| s-unrelated | 1 | What is UConn's University Travel Card suspension rule? | self_contained | delegate | 6 | True | True | ok |
| s-unrelated | 2 | How often must UT Austin user accounts be reviewed? | self_contained | delegate | 6 | True | True | ok |
| s-ambiguous | 1 | How often must UT Austin user accounts be reviewed, and is there a limit on the gratuities Rutgers will reimburse for business travel? | self_contained | fused | 6 | True | True | ok |
| s-ambiguous | 2 | Does that apply currently? | unresolved | – | 0 | True | True | ok |
| s-unsupported | 1 | What are Rutgers' rules for travel advances? | self_contained | delegate | 6 | True | True | ok |
| s-unsupported | 2 | Does Harvard University have the same rule? | unresolved | – | 0 | True | True | ok |

### phase5 (single retrieval, new deployment every turn): 17/17 turns

| conversation | turn | question | resolution | strategy | evidence | = input | = session | checks |
|---|---|---|---|---|---|---|---|---|
| s-direct | 1 | What are Rutgers' rules for travel advances? | self_contained | delegate | 6 | True | True | ok |
| s-direct | 2 | What documentation is required after the trip? | follow_up | delegate | 6 | True | True | ok |
| s-reference | 1 | What is UConn's University Travel Card suspension rule? | self_contained | delegate | 6 | True | True | ok |
| s-reference | 2 | What happens if they are still unresolved after 90 days? | follow_up | delegate | 6 | True | True | ok |
| s-temporal | 1 | What did UConn's February 2026 procedures say about when a University Travel Card is suspended? | self_contained | delegate | 6 | True | True | ok |
| s-temporal | 2 | Does that still apply under the current procedures? | follow_up | delegate | 6 | True | True | ok |
| s-chain | 1 | What is UConn's University Travel Card suspension rule? | self_contained | delegate | 6 | True | True | ok |
| s-chain | 2 | Does that apply currently? | follow_up | delegate | 6 | True | True | ok |
| s-chain | 3 | What happens after 90 days? | follow_up | delegate | 6 | True | True | ok |
| s-constraint | 1 | At Oregon State, what procurement method applies to a $180,000 purchase? | self_contained | delegate | 6 | True | True | ok |
| s-constraint | 2 | What if it is an emergency costing less than $2 million? | follow_up | delegate | 6 | True | True | ok |
| s-unrelated | 1 | What is UConn's University Travel Card suspension rule? | self_contained | delegate | 6 | True | True | ok |
| s-unrelated | 2 | How often must UT Austin user accounts be reviewed? | self_contained | delegate | 6 | True | True | ok |
| s-ambiguous | 1 | How often must UT Austin user accounts be reviewed, and is there a limit on the gratuities Rutgers will reimburse for business travel? | self_contained | fused | 6 | True | True | ok |
| s-ambiguous | 2 | Does that apply currently? | unresolved | – | 0 | True | True | ok |
| s-unsupported | 1 | What are Rutgers' rules for travel advances? | self_contained | delegate | 6 | True | True | ok |
| s-unsupported | 2 | Does Harvard University have the same rule? | unresolved | – | 0 | True | True | ok |

### phase6 (iterative retrieval): 19/19 turns

| conversation | turn | question | resolution | strategy | evidence | = input | = session | checks |
|---|---|---|---|---|---|---|---|---|
| s-direct | 1 | What are Rutgers' rules for travel advances? | self_contained | delegate | 6 | True | True | ok |
| s-direct | 2 | What documentation is required after the trip? | follow_up | delegate | 6 | True | True | ok |
| s-reference | 1 | What is UConn's University Travel Card suspension rule? | self_contained | delegate | 6 | True | True | ok |
| s-reference | 2 | What happens if they are still unresolved after 90 days? | follow_up | delegate | 6 | True | True | ok |
| s-temporal | 1 | What did UConn's February 2026 procedures say about when a University Travel Card is suspended? | self_contained | delegate | 6 | True | True | ok |
| s-temporal | 2 | Does that still apply under the current procedures? | follow_up | delegate | 6 | True | True | ok |
| s-chain | 1 | What is UConn's University Travel Card suspension rule? | self_contained | delegate | 6 | True | True | ok |
| s-chain | 2 | Does that apply currently? | follow_up | delegate | 6 | True | True | ok |
| s-chain | 3 | What happens after 90 days? | follow_up | delegate | 6 | True | True | ok |
| s-constraint | 1 | At Oregon State, what procurement method applies to a $180,000 purchase? | self_contained | delegate | 6 | True | True | ok |
| s-constraint | 2 | What if it is an emergency costing less than $2 million? | follow_up | delegate | 6 | True | True | ok |
| s-unrelated | 1 | What is UConn's University Travel Card suspension rule? | self_contained | delegate | 6 | True | True | ok |
| s-unrelated | 2 | How often must UT Austin user accounts be reviewed? | self_contained | delegate | 6 | True | True | ok |
| s-ambiguous | 1 | How often must UT Austin user accounts be reviewed, and is there a limit on the gratuities Rutgers will reimburse for business travel? | self_contained | fused | 6 | True | True | ok |
| s-ambiguous | 2 | Does that apply currently? | unresolved | – | 0 | True | True | ok |
| s-unsupported | 1 | What are Rutgers' rules for travel advances? | self_contained | delegate | 6 | True | True | ok |
| s-unsupported | 2 | Does Harvard University have the same rule? | unresolved | – | 0 | True | True | ok |
| s6-uc-ru-scope | 1 | At UConn, what is the University Travel Card suspension rule, and at Rutgers, what is the travel advance rule? | self_contained | delegate | 6 | True | True | ok |
| s6-harvard-penn-scope | 1 | At Harvard University, what is the procurement policy, and at Penn, what is the procurement policy? | self_contained | delegate | 6 | True | True | ok |
