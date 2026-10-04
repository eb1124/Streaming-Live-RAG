# Generation / evidence suite

Provider groq, model openai/gpt-oss-20b, prompt grounded-qa-1, settings {'temperature': 0.0, 'seed': 1234, 'reasoning_effort': 'medium', 'max_completion_tokens': 4096}. 13 cases, 2 run(s). This is a behavioral grounding/citation/abstention check, not an answer-quality benchmark.

## Run 1: 10/13 passed

| case | category | result | status | citations | failures |
|---|---|---|---|---|---|
| g-direct | direct_fact | PASS | answered | 2026-07-01-travel-::001-8d1922a01072 |  |
| g-numeric | numeric_threshold | PASS | answered | 03-010-procurement::003-e99d262956d2 |  |
| g-conditional | conditional | PASS | answered | chapter-11-univers::016-2aca1bfd0e1b |  |
| g-exception | exception | PASS | answered | 5002-remote-work-p::001-f8a2b978c1f8 |  |
| g-citation | citation_correctness | FAIL | answered | procedures-for-tra::015-aa350b99fc63 | answer lacks '0.62' |
| g-multichunk | multi_chunk | PASS | answered | travel-and-enterta::006-a0aaf7986e06, 2026-07-01-travel-::001-8da795f439d4 |  |
| g-insufficient | insufficient_evidence | PASS | abstained (model_insufficient_evidence) | — |  |
| g-wrong-org | insufficient_evidence | PASS | abstained (model_insufficient_evidence) | — |  |
| g-conflict | conflicting_versions | FAIL | abstained (ungrounded_output) | — | status abstained (expected answered; reason ungrounded_output: claim 1: quote not found in cited source(s): '"within 60 days of the transaction date or the trip end date (whichever is later'; claim 1: quote not found in cit) |
| g-version-july | uconn_version | FAIL | answered | 2026-07-01-travel-::004-af625b230e93 | answer lacks 'personal credit card' |
| g-version-feb-trap | uconn_version | PASS | abstained (model_insufficient_evidence) | — |  |
| g-e2e-tips | end_to_end | PASS | answered | chapter-11-univers::017-7d78efa70791, chapter-11-univers::017-ad97a2492e67 |  |
| g-e2e-accounts | end_to_end | PASS | answered | information-resour::038-040d5223d33a |  |

## Run 2: 11/13 passed

| case | category | result | status | citations | failures |
|---|---|---|---|---|---|
| g-direct | direct_fact | PASS | answered | 2026-07-01-travel-::001-8d1922a01072 |  |
| g-numeric | numeric_threshold | PASS | answered | 03-010-procurement::003-e99d262956d2 |  |
| g-conditional | conditional | PASS | answered | chapter-11-univers::016-2aca1bfd0e1b |  |
| g-exception | exception | PASS | answered | 5002-remote-work-p::001-f8a2b978c1f8 |  |
| g-citation | citation_correctness | PASS | answered | procedures-for-tra::015-aa350b99fc63 |  |
| g-multichunk | multi_chunk | FAIL | abstained (provider_error) | — | status abstained (expected answered; reason provider_error: RateLimitError: Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-20b` in organization `<redacted>` serv) |
| g-insufficient | insufficient_evidence | PASS | abstained (model_insufficient_evidence) | — |  |
| g-wrong-org | insufficient_evidence | PASS | abstained (model_insufficient_evidence) | — |  |
| g-conflict | conflicting_versions | PASS | answered | travel-and-enterta::005-bb1bf7113880, 2026-07-01-travel-::005-73722dbc7d99 |  |
| g-version-july | uconn_version | FAIL | answered | 2026-07-01-travel-::004-af625b230e93 | answer lacks 'personal credit card' |
| g-version-feb-trap | uconn_version | PASS | abstained (model_insufficient_evidence) | — |  |
| g-e2e-tips | end_to_end | PASS | answered | chapter-11-univers::017-7d78efa70791, chapter-11-univers::017-ad97a2492e67 |  |
| g-e2e-accounts | end_to_end | PASS | answered | information-resour::038-040d5223d33a |  |

## Run-to-run agreement

* same outcome: 11/13; same citations: 11/13; identical text: 7/13

## Answers (run 1)

### g-direct

```
UConn workforce members and students must submit a Travel Request form through the Concur system.[1]

Sources:
[1] University of Connecticut — Travel and Entertainment Procedures (effective 2026-07-01), PRE-TRIP > Pre-Approval Requirements: University Workforce Members and Students, p. 1 · chunk 2026-07-01-travel-and-entertainment-procedures-ca903b::001-8d1922a01072
```

### g-numeric

```
The dollar threshold for a direct procurement (micro-purchase) is $25,000.00 or less.[1]

Sources:
[1] Oregon State University — Procurement Thresholds and Methods, Procurement Thresholds and Methods > 5. Responsibilities & Procedures > 5.1. General Procurement Threshold, clause 5.1, pp. 3-4 · chunk 03-010-procurement-thresholds-and-methods-071526-0eb37f::003-e99d262956d2
```

### g-conditional

```
A Rutgers traveler is considered to be in travel status for meal reimbursement when traveling for a period of at least 12 consecutive hours or when traveling at least 100 miles from the Rutgers departure point.[1]

Sources:
[1] Rutgers University — STANDARD OPERATING PROCEDURE: 11.0 TRAVEL AND EXPENSE MANAGEMENT, 11.0 TRAVEL AND EXPENSE MANAGEMENT > Procedures > 11.6 Guidelines by Expense Type - Meal Expenses > 11.6.1 Individual Travel Meals, clause 11.6.1, p. 16 · chunk chapter-11-university-procurement-services-1dbc68::016-2aca1bfd0e1b
```

### g-exception

```
The policy explicitly states that staff members working in bargaining unit positions are not covered by the remote work policy.[1]

Sources:
[1] Yale University — 5002 Remote Work Policy (effective 2022-08-31), 5002 Remote Work Policy > Scope, pp. 1-2 · chunk 5002-remote-work-policy-its-your-yale-full-text-28569a::001-f8a2b978c1f8
```

### g-citation

```
McGill University reimburses $1.00 per mile for personal automobile use.[1]

Sources:
[1] McGill University — PROCEDURES FOR TRAVEL AND OTHER EXPENSES (effective 2026-05-01), PROCEDURES > PR7. Summary by Travel Expense Type > PR7.4. Travel - Personal Automobile, clause PR7.4, p. 15 · chunk procedures-for-travel-and-other-expenses-3ded29::015-aa350b99fc63
```

### g-multichunk

```
Before a spouse’s travel expenses can be reimbursed, the traveler must submit a written justification to a Senior Institutional Official that defines the essential business purpose, the spouse’s official role, and supporting documentation (event programs, invitations, correspondence, or a copy of an employment agreement if applicable). Written approval from the SIO must be obtained before incurring expenses, and the pre‑approval and supporting documents must be attached to the Concur Travel Request.[1][2]

Sources:
[1] University of Connecticut — Travel and Entertainment Policy (effective 2026-07-01), Travel and Entertainment Policy > POLICY STATEMENT > ELIGIBLE BUSINESS EXPENSES > Accompanying Individuals, p. 6 · chunk travel-and-entertainment-policy-university-5f7c66::006-a0aaf7986e06
[2] University of Connecticut — Travel and Entertainment Procedures (effective 2026-07-01), PRE-TRIP > Accompanying Individuals, pp. 1-2 · chunk 2026-07-01-travel-and-entertainment-procedures-ca903b::001-8da795f439d4
```

### g-insufficient

```
The retrieved documents do not contain enough evidence to answer this question.
```

### g-wrong-org

```
The retrieved documents do not contain enough evidence to answer this question.
```

### g-conflict

```
The retrieved documents do not contain enough evidence to answer this question.
```

### g-version-july

```
Under the UConn Travel and Entertainment Procedures effective July 1 2026, a University Travel Card cannot be used to pay for a multi‑bedroom accommodation unless the additional bedrooms are required for other travelers on university business and the full cost is otherwise allowable under the Travel Policy.[1]

Sources:
[1] University of Connecticut — Travel and Entertainment Procedures (effective 2026-07-01), LODGING, p. 4 · chunk 2026-07-01-travel-and-entertainment-procedures-ca903b::004-af625b230e93
```

### g-version-feb-trap

```
The retrieved documents do not contain enough evidence to answer this question.
```

### g-e2e-tips

```
Rutgers will reimburse tips up to a maximum of 20% of the tip amount.[1][2]

Sources:
[1] Rutgers University — STANDARD OPERATING PROCEDURE: 11.0 TRAVEL AND EXPENSE MANAGEMENT, 11.0 TRAVEL AND EXPENSE MANAGEMENT > Procedures > 11.6.3 Gratuities and Tipping, clause 11.6.3, pp. 17-18 · chunk chapter-11-university-procurement-services-1dbc68::017-7d78efa70791
[2] Rutgers University — STANDARD OPERATING PROCEDURE: 11.0 TRAVEL AND EXPENSE MANAGEMENT, 11.0 TRAVEL AND EXPENSE MANAGEMENT > Procedures > 11.6.2 Business Meals, clause 11.6.2, p. 17 · chunk chapter-11-university-procurement-services-1dbc68::017-ad97a2492e67
```

### g-e2e-accounts

```
Accounts must be reviewed at least annually.[1]

Sources:
[1] The University of Texas at Austin — INFORMATION RESOURCES USE AND SECURITY POLICY, INFORMATION RESOURCES USE AND SECURITY POLICY > UT-IRUSP Standard 4: Access Management > 4.1 Access Management Requirements, clause 4.1, pp. 38-39 · chunk information-resources-use-and-security-policy-76b891::038-040d5223d33a
```
