# Temporal / version resolution evaluation

18 cases (UConn Travel and Entertainment Procedures, February vs July 2026). Separate from the frozen retrieval benchmark. Gold rank = rank at which all gold evidence units are in the top 10 (— = not all present).

* Intent detected correctly: **18/18**; requested version selected correctly: **18/18**.

## Hybrid RRF: before → after

* cases with any wrong-version chunk in the top 10: 12 → 0
* gold evidence at rank 1: 9 → 11; all gold units in top 10: 16 → 16 (of 17 cases with gold)
* neutral cases with identical top 10: 3/3

| case | category | intent | selected | gold rank | wrong-version chunks in top 10 | first procedures version in top 10 |
|---|---|---|---|---|---|---|
| t-jul-lodging | explicit_july | point_in_time | jul | 1 → 1 | 1 → 0 | jul → jul |
| t-jul-card-deadline | explicit_july | point_in_time | jul | 1 → 1 | 3 → 0 | jul → jul |
| t-jul-approval | explicit_july | point_in_time | jul | 1 → 1 | 3 → 0 | jul → jul |
| t-jul-asof | explicit_july | point_in_time | jul | 4 → 3 | 2 → 0 | jul → jul |
| t-feb-suspension | explicit_february | point_in_time | feb | 4 → 2 | 3 → 0 | feb → feb |
| t-feb-approval | explicit_february | point_in_time | feb | 2 → 1 | 3 → 0 | jul → feb |
| t-feb-cars | explicit_february | point_in_time | feb | 2 → 1 | 2 → 0 | jul → feb |
| t-cur-suspension | current | current | jul | 1 → 1 | 3 → 0 | jul → jul |
| t-cur-tickets | current | current | jul | 1 → 1 | 3 → 0 | jul → jul |
| t-cur-athletics | current | current | jul | 1 → 1 | 3 → 0 | jul → jul |
| t-neu-sio | neutral | neutral | — | 1 → 1 | 0 → 0 | jul → jul |
| t-neu-advance | neutral | neutral | — | 1 → 1 | 0 → 0 | jul → jul |
| t-neu-oregon | neutral | neutral | — | 1 → 1 | 0 → 0 | — → — |
| t-cmp-suspension | conflict | compare | jul,feb | 9 → 9 | 0 → 0 | jul → jul |
| t-cmp-lodging | conflict | compare | jul,feb | — → — | 0 → 0 | jul → jul |
| t-one-reinstate-feb | one_version_only | point_in_time | feb | 4 → 2 | 3 → 0 | jul → feb |
| t-one-tickets-march | one_version_only | point_in_time | feb | 6 → 4 | 4 → 0 | jul → feb |
| t-one-unavailable | one_version_only | point_in_time | none (unavailable) | — → — | 0 → 0 | feb → — |

## Hybrid + rerank: before → after

* cases with any wrong-version chunk in the top 10: 12 → 0
* gold evidence at rank 1: 8 → 11; all gold units in top 10: 15 → 15 (of 17 cases with gold)
* neutral cases with identical top 10: 3/3

| case | category | intent | selected | gold rank | wrong-version chunks in top 10 | first procedures version in top 10 |
|---|---|---|---|---|---|---|
| t-jul-lodging | explicit_july | point_in_time | jul | 3 → 2 | 1 → 0 | jul → jul |
| t-jul-card-deadline | explicit_july | point_in_time | jul | 1 → 1 | 4 → 0 | jul → jul |
| t-jul-approval | explicit_july | point_in_time | jul | 1 → 1 | 3 → 0 | jul → jul |
| t-jul-asof | explicit_july | point_in_time | jul | 5 → 4 | 2 → 0 | jul → jul |
| t-feb-suspension | explicit_february | point_in_time | feb | 2 → 2 | 4 → 0 | feb → feb |
| t-feb-approval | explicit_february | point_in_time | feb | 1 → 1 | 4 → 0 | feb → feb |
| t-feb-cars | explicit_february | point_in_time | feb | 2 → 1 | 3 → 0 | jul → feb |
| t-cur-suspension | current | current | jul | 2 → 1 | 3 → 0 | feb → jul |
| t-cur-tickets | current | current | jul | 1 → 1 | 3 → 0 | jul → jul |
| t-cur-athletics | current | current | jul | 1 → 1 | 4 → 0 | jul → jul |
| t-neu-sio | neutral | neutral | — | 1 → 1 | 0 → 0 | feb → feb |
| t-neu-advance | neutral | neutral | — | 1 → 1 | 0 → 0 | jul → jul |
| t-neu-oregon | neutral | neutral | — | 1 → 1 | 0 → 0 | — → — |
| t-cmp-suspension | conflict | compare | jul,feb | 4 → 4 | 0 → 0 | jul → jul |
| t-cmp-lodging | conflict | compare | jul,feb | — → — | 0 → 0 | jul → jul |
| t-one-reinstate-feb | one_version_only | point_in_time | feb | 2 → 1 | 4 → 0 | jul → feb |
| t-one-tickets-march | one_version_only | point_in_time | feb | — → — | 4 → 0 | jul → feb |
| t-one-unavailable | one_version_only | point_in_time | none (unavailable) | — → — | 0 → 0 | feb → — |

