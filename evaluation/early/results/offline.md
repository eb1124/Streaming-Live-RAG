# Early-retrieval evaluation (no language model)

Real retrieval stack, stub model, a word every 400 ms; see evaluation/early/run.py for the method and the definitions. The eligible utterances are the questions of the existing suites; whether an utterance is eligible is the controller's own decision on the complete utterance.

## Summary

* Utterances: 82; eligible (retrieval required when complete): **60**.
* Retrieval triggered before the utterance was complete: **59/60 = 98.3%**.
* Triggered and the early retrieval also finished before completion (measured wall time on this machine): 57/60 = 95.0%.
* Utterances that required no retrieval: 22 (presentation: 6, retrieve: 2, suppress: 8, wait: 6); false triggers: **1/22 = 4.5%**.
* Mean lead of the trigger over completion: 5389.8 ms; mean early retrieval wall time: 659.6 ms.
* Early context vs the context the complete utterance was answered from: mean overlap 0.307; some overlap in 78.0% of the early retrievals.

| suite | utterances | eligible | early | false triggers |
|---|---|---|---|---|
| integration | 27 | 27 | 27 | 0 |
| multi_intent | 16 | 16 | 16 | 0 |
| streaming | 2 | 2 | 2 | 0 |
| session | 17 | 15 | 14 | 1 |
| acknowledgement | 8 | 0 | 0 | 0 |
| layout_request | 6 | 0 | 0 | 0 |
| unfinished | 6 | 0 | 0 | 0 |

## Utterances

| suite | case | utterance | words | completion ms | decision | required | trigger word | trigger ms | lead ms | retrieval ms | overlap | partial transcript at the trigger |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| integration | i-uc-jul-submit | Under UConn's travel procedures effective July 1, 2026, within how ... | 25 | 10000 | retrieve | True | 3 | 1200 | 8800 | 548.5 | 0.167 | Under UConn's travel |
| integration | i-uc-feb-suspend | According to UConn's February 2026 Travel and Entertainment Procedu... | 24 | 9600 | retrieve | True | 3 | 1200 | 8400 | 501.4 | 0.167 | According to UConn's |
| integration | i-uc-jul-owner | Who owns UConn's Travel and Entertainment Procedures effective July... | 18 | 7200 | retrieve | True | 3 | 1200 | 6000 | 636.4 | 0.5 | Who owns UConn's |
| integration | i-uc-cur-reinstate | How does UConn currently reinstate a suspended University Travel Card? | 10 | 4000 | retrieve | True | 4 | 1600 | 2400 | 678.4 | 0.667 | How does UConn currently |
| integration | i-uc-cur-multibed | Under UConn's current procedures, may a traveler pay for a multi-be... | 17 | 6800 | retrieve | True | 3 | 1200 | 5600 | 524.4 | 0.167 | Under UConn's current |
| integration | i-uc-cmp-card | What did UConn's travel procedures effective February 1, 2026 and t... | 25 | 10000 | retrieve | True | 4 | 1600 | 8400 | 482.1 | 0.333 | What did UConn's travel |
| integration | i-uc-neutral-card | How long do UConn travelers have to submit University Travel Card c... | 17 | 6800 | retrieve | True | 4 | 1600 | 5200 | 521.6 | 0.0 | How long do UConn |
| integration | i-uc-cmp-lodging | Compare the multi-bedroom accommodation rules in UConn's procedures... | 16 | 6400 | retrieve | True | 3 | 1200 | 5200 | 743.9 | 0.167 | Compare the multi-bedroom |
| integration | i-uc-march-tickets | Did the UConn travel procedures in effect on March 15, 2026 require... | 18 | 7200 | retrieve | True | 4 | 1600 | 5600 | 665.2 | 0.333 | Did the UConn travel |
| integration | i-uc-feb-reinstate | Under UConn's February 2026 procedures, what must a cardholder do t... | 18 | 7200 | retrieve | True | 3 | 1200 | 6000 | 511.8 | 0.5 | Under UConn's February |
| integration | i-uc-unavailable | What did UConn's travel procedures effective January 1, 2025 say ab... | 13 | 5200 | retrieve | True | 4 | 1600 | 3600 | 509.8 | 0.0 | What did UConn's travel |
| integration | i-uc-neu-sio | How long can a UConn business trip last before Senior Institutional... | 15 | 6000 | retrieve | True | 5 | 2000 | 4000 | 516.0 | 0.0 | How long can a UConn |
| integration | i-uc-spouse | What must a UConn traveler do before a spouse's or partner's travel... | 16 | 6400 | retrieve | True | 5 | 2000 | 4400 | 656.4 | 0.667 | What must a UConn traveler |
| integration | i-uc-personal-leg | If a UConn traveler adds a personal leg to a business flight, what ... | 23 | 9200 | retrieve | True | 4 | 1600 | 7600 | 663.3 | 0.5 | If a UConn traveler |
| integration | i-ru-tips | Is there a limit on the gratuities Rutgers will reimburse for busin... | 13 | 5200 | retrieve | True | 4 | 1600 | 3600 | 814.3 | 0.0 | Is there a limit |
| integration | i-ru-advance | How far in advance of departure must a Rutgers travel advance reque... | 14 | 5600 | retrieve | True | 4 | 1600 | 4000 | 790.1 | 0.5 | How far in advance |
| integration | i-or-180k | Oregon State expects a contract price of $180,000. Which general pr... | 19 | 7600 | retrieve | True | 2 | 800 | 6800 | 868.2 | 0.0 | Oregon State |
| integration | i-st-invoice | Under what conditions can Stanford pay an invoice that exceeds the ... | 19 | 7600 | retrieve | True | 5 | 2000 | 5600 | 499.8 | 0.5 | Under what conditions can Stanford |
| integration | i-pe-exception | Does Penn require competitive bids for a $75,000 purchase from a Pr... | 14 | 5600 | retrieve | True | 3 | 1200 | 4400 | 783.0 | 0.333 | Does Penn require |
| integration | i-ut-clause | Which clause of UT Austin's information resources policy sets how o... | 21 | 8400 | retrieve | True | 4 | 1600 | 6800 | 705.1 | 0.0 | Which clause of UT |
| integration | i-mi-mileage | What mileage rate does the University of Michigan reimburse for bus... | 20 | 8000 | retrieve | True | 3 | 1200 | 6800 | 684.3 | 0.5 | What mileage rate |
| integration | i-ro-highrisk | A University of Rochester researcher plans travel to a high-risk lo... | 20 | 8000 | retrieve | True | 4 | 1600 | 6400 | 730.9 | 0.333 | A University of Rochester |
| integration | i-ya-internet | Does Yale reimburse home internet costs for staff with an approved ... | 14 | 5600 | retrieve | True | 3 | 1200 | 4400 | 825.6 | 0.167 | Does Yale reimburse |
| integration | i-st-mileage | What mileage rate does Stanford University reimburse for personal c... | 11 | 4400 | retrieve | True | 3 | 1200 | 3200 | 712.6 | 1.0 | What mileage rate |
| integration | i-harvard-bids | What purchase amount requires competitive bids at Harvard University? | 9 | 3600 | retrieve | True | 3 | 1200 | 2400 | 496.0 | 0.167 | What purchase amount |
| integration | i-ro-tips | What is the maximum tip percentage the University of Rochester will... | 12 | 4800 | retrieve | True | 5 | 2000 | 2800 | 682.0 | 0.167 | What is the maximum tip |
| integration | i-uc-nyc-hotel | What is the maximum nightly hotel rate UConn will reimburse in New ... | 14 | 5600 | retrieve | True | 5 | 2000 | 3600 | 669.0 | 0.167 | What is the maximum nightly |
| multi_intent | mi-uc-card | What is the UConn University Travel Card submission deadline, and w... | 18 | 7200 | retrieve | True | 5 | 2000 | 5200 | 440.9 | 0.167 | What is the UConn University |
| multi_intent | mi-penn | At Penn, what happens to purchase orders of $50,000 or more before ... | 20 | 8000 | retrieve | True | 4 | 1600 | 6400 | 681.7 | 0.5 | At Penn, what happens |
| multi_intent | mi-mi-air | What airfare does the University of Michigan pay for, and how far i... | 22 | 8800 | retrieve | True | 5 | 2000 | 6800 | 686.2 | 0.167 | What airfare does the University |
| multi_intent | mi-or-emergency | At Oregon State, what procurement method applies to a $180,000 purc... | 29 | 11600 | retrieve | True | 3 | 1200 | 10400 | 688.2 | 0.0 | At Oregon State, |
| multi_intent | mi-ut-ru | How often must UT Austin user accounts be reviewed, and is there a ... | 23 | 9200 | retrieve | True | 4 | 1600 | 7600 | 723.4 | 0.5 | How often must UT |
| multi_intent | mi-st-ro | Under what conditions can Stanford pay an invoice that exceeds the ... | 38 | 15200 | retrieve | True | 5 | 2000 | 13200 | 537.0 | 0.167 | Under what conditions can Stanford |
| multi_intent | mi-mi-ru-dated | What mileage rate does the University of Michigan reimburse for bus... | 35 | 14000 | retrieve | True | 3 | 1200 | 12800 | 629.7 | 0.167 | What mileage rate |
| multi_intent | mi-three | How often must UT Austin user accounts be reviewed, what mileage ra... | 40 | 16000 | retrieve | True | 4 | 1600 | 14400 | 752.6 | 0.333 | How often must UT |
| multi_intent | mi-uc-mixed-temporal | What did UConn's February 2026 procedures say about when a Universi... | 30 | 12000 | retrieve | True | 4 | 1600 | 10400 | 472.1 | 0.25 | What did UConn's February |
| multi_intent | mi-uc-jul-scope | Under UConn's procedures effective July 1, 2026, how is a suspended... | 28 | 11200 | retrieve | True | 3 | 1200 | 10000 | 463.3 | 0.167 | Under UConn's procedures |
| multi_intent | mi-ro-harvard | How far ahead should a University of Rochester traveler submit a re... | 28 | 11200 | retrieve | True | 3 | 1200 | 10000 | 573.6 | 0.167 | How far ahead |
| multi_intent | mi-ru-yale | How far in advance of departure must a Rutgers travel advance reque... | 26 | 10400 | retrieve | True | 4 | 1600 | 8800 | 584.2 | 0.167 | How far in advance |
| multi_intent | mi-ctl-single | What does the University of Rochester policy say about tipping reim... | 11 | 4400 | retrieve | True | 6 | 2400 | 2000 | 772.7 | 0.5 | What does the University of Rochester |
| multi_intent | mi-ctl-explain | At Stanford, can a $240 invoice overage on a $2,000 standard purcha... | 22 | 8800 | retrieve | True | 6 | 2400 | 6400 | 668.1 | 0.667 | At Stanford, can a $240 invoice |
| multi_intent | mi-ctl-refers | Which clause of UT Austin's information resources policy sets how o... | 21 | 8400 | retrieve | True | 4 | 1600 | 6800 | 746.9 | 0.0 | Which clause of UT |
| multi_intent | mi-ctl-compare | Compare the February 1, 2026 and July 1, 2026 UConn Travel Card pen... | 14 | 5600 | retrieve | True | 3 | 1200 | 4400 | 678.3 | 0.167 | Compare the February |
| streaming | s6-uc-ru-scope | At UConn, what is the University Travel Card suspension rule, and a... | 19 | 7600 | retrieve | True | 6 | 2400 | 5200 | 483.6 | 0.0 | At UConn, what is the University |
| streaming | s6-harvard-penn-scope | At Harvard University, what is the procurement policy, and at Penn,... | 16 | 6400 | retrieve | True | 3 | 1200 | 5200 | 625.2 | 0.0 | At Harvard University, |
| session | s-direct | What are Rutgers' rules for travel advances? | 7 | 2800 | retrieve | True | 4 | 1600 | 1200 | 703.5 | 0.0 | What are Rutgers' rules |
| session | s-direct | What documentation is required after the trip? | 7 | 2800 | retrieve | True | 4 | 1600 | 1200 | 689.8 | 1.0 | What documentation is required |
| session | s-reference | What is UConn's University Travel Card suspension rule? | 8 | 3200 | retrieve | True | 4 | 1600 | 1600 | 501.5 | 0.333 | What is UConn's University |
| session | s-reference | What happens if they are still unresolved after 90 days? | 10 | 4000 | retrieve | True | 6 | 2400 | 1600 | 569.7 | 0.667 | What happens if they are still |
| session | s-temporal | What did UConn's February 2026 procedures say about when a Universi... | 15 | 6000 | retrieve | True | 4 | 1600 | 4400 | 490.1 | 0.5 | What did UConn's February |
| session | s-temporal | Does that still apply under the current procedures? | 8 | 3200 | retrieve | True | 7 | 2800 | 400 | 1488.8 | 0.5 | Does that still apply under the current |
| session | s-chain | What is UConn's University Travel Card suspension rule? | 8 | 3200 | retrieve | True | 4 | 1600 | 1600 | 485.9 | 0.333 | What is UConn's University |
| session | s-chain | Does that apply currently? | 4 | 1600 | retrieve | True | – | – | – | – | – | – |
| session | s-chain | What happens after 90 days? | 5 | 2000 | retrieve | True | 3 | 1200 | 800 | 1240.2 | 1.0 | What happens after |
| session | s-constraint | At Oregon State, what procurement method applies to a $180,000 purc... | 11 | 4400 | retrieve | True | 3 | 1200 | 3200 | 723.1 | 0.167 | At Oregon State, |
| session | s-constraint | What if it is an emergency costing less than $2 million? | 11 | 4400 | retrieve | True | 7 | 2800 | 1600 | 765.5 | 0.667 | What if it is an emergency costing |
| session | s-unrelated | What is UConn's University Travel Card suspension rule? | 8 | 3200 | retrieve | True | 4 | 1600 | 1600 | 492.0 | 0.333 | What is UConn's University |
| session | s-unrelated | How often must UT Austin user accounts be reviewed? | 9 | 3600 | retrieve | True | 4 | 1600 | 2000 | 683.7 | 0.0 | How often must UT |
| session | s-ambiguous | How often must UT Austin user accounts be reviewed, and is there a ... | 23 | 9200 | retrieve | True | 4 | 1600 | 7600 | 768.6 | 0.5 | How often must UT |
| session | s-ambiguous | Does that apply currently? | 4 | 1600 | retrieve | False | – | – | – | – | – | – |
| session | s-unsupported | What are Rutgers' rules for travel advances? | 7 | 2800 | retrieve | True | 4 | 1600 | 1200 | 686.6 | 0.0 | What are Rutgers' rules |
| session | s-unsupported | Does Harvard University have the same rule? | 7 | 2800 | retrieve | False | 3 | 1200 | 1600 | 585.8 | – | Does Harvard University |
| acknowledgement | ack-1 | Thanks, that's all. | 3 | 1200 | suppress | False | – | – | – | – | – | – |
| acknowledgement | ack-2 | Thank you very much! | 4 | 1600 | suppress | False | – | – | – | – | – | – |
| acknowledgement | ack-3 | Ok, got it. | 3 | 1200 | suppress | False | – | – | – | – | – | – |
| acknowledgement | ack-4 | Great, thanks for the help. | 5 | 2000 | suppress | False | – | – | – | – | – | – |
| acknowledgement | ack-5 | Perfect. No more questions. | 4 | 1600 | suppress | False | – | – | – | – | – | – |
| acknowledgement | ack-6 | That's all for now, thank you. | 6 | 2400 | suppress | False | – | – | – | – | – | – |
| acknowledgement | ack-7 | Understood, thanks. | 2 | 800 | suppress | False | – | – | – | – | – | – |
| acknowledgement | ack-8 | Okay, that makes sense. Thanks! | 5 | 2000 | suppress | False | – | – | – | – | – | – |
| layout_request | layout-1 | Give me that in two bullet points. | 7 | 2800 | presentation | False | – | – | – | – | – | – |
| layout_request | layout-2 | Can you put that in 3 bullets? | 7 | 2800 | presentation | False | – | – | – | – | – | – |
| layout_request | layout-3 | Show me the answer as a numbered list. | 8 | 3200 | presentation | False | – | – | – | – | – | – |
| layout_request | layout-4 | Rewrite it in three points. | 5 | 2000 | presentation | False | – | – | – | – | – | – |
| layout_request | layout-5 | In bullet points please. | 4 | 1600 | presentation | False | – | – | – | – | – | – |
| layout_request | layout-6 | Please give me that again as a numbered list. | 9 | 3600 | presentation | False | – | – | – | – | – | – |
| unfinished | unfinished-1 | What is the... | 3 | 1200 | wait | False | – | – | – | – | – | – |
| unfinished | unfinished-2 | What are the | 3 | 1200 | wait | False | – | – | – | – | – | – |
| unfinished | unfinished-3 | How does | 2 | 800 | wait | False | – | – | – | – | – | – |
| unfinished | unfinished-4 | What is the UConn... | 4 | 1600 | wait | False | – | – | – | – | – | – |
| unfinished | unfinished-5 | For Rutgers, what is the | 5 | 2000 | wait | False | – | – | – | – | – | – |
| unfinished | unfinished-6 | And what about the | 4 | 1600 | wait | False | – | – | – | – | – | – |
