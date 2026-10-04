# Phase 8 offline evaluation (MCP tools; no language model, no network)

Every call goes through the MCP protocol (mcp.Client in memory → services.mcp.server). Real retrieval stack (arctic-m + BM25 + RRF, temporal resolution, cross-encoder rerank), real repository metadata. See evaluation/mcp/run.py.

Corpus: 12 documents, 466 chunks. Tools listed: ['document_search', 'metadata_lookup', 'database_query'].

## 121/121 checks passed

### search: 43/43

| case | temporal | selected | results | gold in top 10 | top result (org, file, pages, clause, effective, score) | checks |
|---|---|---|---|---|---|---|
| i-uc-jul-submit | point_in_time | {'uconn-travel-entertainment-procedures': ['2026-07-01-travel-and-entertainment-procedures-ca903b']} | 10 | 1/1 | 2026-07-01-travel-and-entertainment-procedures-ca903b::005-73722dbc7d99 (University of Connecticut, 2026-07-01-Travel-and-Entertainment-Procedures.pdf, p[5], None, 2026-07-01, 6.62) | ok |
| i-uc-feb-suspend | point_in_time | {'uconn-travel-entertainment-procedures': ['travel-and-entertainment-procedures-final-ccccf9']} | 10 | 1/1 | travel-and-entertainment-procedures-final-ccccf9::005-bb1bf7113880 (University of Connecticut, Travel-and-Entertainment-Procedures-FINAL.pdf, p[5], None, 2026-02-01, 5.92) | ok |
| i-uc-jul-owner | point_in_time | {'uconn-travel-entertainment-procedures': ['2026-07-01-travel-and-entertainment-procedures-ca903b']} | 10 | 1/1 | 2026-07-01-travel-and-entertainment-procedures-ca903b::001-b18d26ad0b00 (University of Connecticut, 2026-07-01-Travel-and-Entertainment-Procedures.pdf, p[1], None, 2026-07-01, 9.05) | ok |
| i-uc-cur-reinstate | current | {'uconn-travel-entertainment-procedures': ['2026-07-01-travel-and-entertainment-procedures-ca903b']} | 10 | 1/1 | 2026-07-01-travel-and-entertainment-procedures-ca903b::005-73722dbc7d99 (University of Connecticut, 2026-07-01-Travel-and-Entertainment-Procedures.pdf, p[5], None, 2026-07-01, 5.58) | ok |
| i-uc-cur-multibed | current | {'uconn-travel-entertainment-procedures': ['2026-07-01-travel-and-entertainment-procedures-ca903b']} | 10 | 1/1 | 2026-07-01-travel-and-entertainment-procedures-ca903b::004-af625b230e93 (University of Connecticut, 2026-07-01-Travel-and-Entertainment-Procedures.pdf, p[4], None, 2026-07-01, 5.40) | ok |
| i-uc-cmp-card | compare | {'uconn-travel-entertainment-procedures': ['2026-07-01-travel-and-entertainment-procedures-ca903b', 'travel-and-entertainment-procedures-final-ccccf9']} | 10 | 2/2 | 2026-07-01-travel-and-entertainment-procedures-ca903b::001-b18d26ad0b00 (University of Connecticut, 2026-07-01-Travel-and-Entertainment-Procedures.pdf, p[1], None, 2026-07-01, 4.90) | ok |
| i-uc-neutral-card | neutral | {} | 10 | 2/2 | 2026-07-01-travel-and-entertainment-procedures-ca903b::005-73722dbc7d99 (University of Connecticut, 2026-07-01-Travel-and-Entertainment-Procedures.pdf, p[5], None, 2026-07-01, 8.14) | ok |
| i-uc-cmp-lodging | compare | {'uconn-travel-entertainment-procedures': ['2026-07-01-travel-and-entertainment-procedures-ca903b', 'travel-and-entertainment-procedures-final-ccccf9']} | 10 | 1/1 | 2026-07-01-travel-and-entertainment-procedures-ca903b::001-b18d26ad0b00 (University of Connecticut, 2026-07-01-Travel-and-Entertainment-Procedures.pdf, p[1], None, 2026-07-01, 2.97) | ok |
| i-uc-march-tickets | point_in_time | {'uconn-travel-entertainment-procedures': ['travel-and-entertainment-procedures-final-ccccf9']} | 10 | 1/1 | travel-and-entertainment-procedures-final-ccccf9::001-fc82968e5020 (University of Connecticut, Travel-and-Entertainment-Procedures-FINAL.pdf, p[1], None, 2026-02-01, 1.16) | ok |
| i-uc-feb-reinstate | point_in_time | {'uconn-travel-entertainment-procedures': ['travel-and-entertainment-procedures-final-ccccf9']} | 10 | 1/1 | travel-and-entertainment-procedures-final-ccccf9::005-bb1bf7113880 (University of Connecticut, Travel-and-Entertainment-Procedures-FINAL.pdf, p[5], None, 2026-02-01, 2.60) | ok |
| i-uc-unavailable | point_in_time | {'uconn-travel-entertainment-procedures': []} | 10 | – | travel-and-entertainment-policy-university-5f7c66::002-f93cb786c1a0 (University of Connecticut, Travel and Entertainment Policy _ University Policies _ University of Connecticut (capture 2026-09-25).pdf, p[2], None, 2026-07-01, 1.46) | ok |
| i-uc-neu-sio | neutral | {} | 10 | 1/1 | 2026-07-01-travel-and-entertainment-procedures-ca903b::002-3ca0022cdaba (University of Connecticut, 2026-07-01-Travel-and-Entertainment-Procedures.pdf, p[2], None, 2026-07-01, 3.03) | ok |
| i-uc-spouse | neutral | {} | 10 | 2/2 | travel-and-entertainment-policy-university-5f7c66::006-a0aaf7986e06 (University of Connecticut, Travel and Entertainment Policy _ University Policies _ University of Connecticut (capture 2026-09-25).pdf, p[6], None, 2026-07-01, 5.72) | ok |
| i-uc-personal-leg | neutral | {} | 10 | 1/1 | travel-and-entertainment-policy-university-5f7c66::006-88a5f45d99ac (University of Connecticut, Travel and Entertainment Policy _ University Policies _ University of Connecticut (capture 2026-09-25).pdf, p[6], None, 2026-07-01, 5.66) | ok |
| i-ru-tips | neutral | {} | 10 | 1/1 | chapter-11-university-procurement-services-1dbc68::017-7d78efa70791 (Rutgers University, Chapter 11 University Procurement Services Procedures Manual.pdf, p[17, 18], 11.6.3, None, 4.66) | ok |
| i-ru-advance | neutral | {} | 10 | 1/1 | 2026-07-01-travel-and-entertainment-procedures-ca903b::002-1483518e1be0 (University of Connecticut, 2026-07-01-Travel-and-Entertainment-Procedures.pdf, p[2], None, 2026-07-01, 5.67) | ok |
| i-or-180k | neutral | {} | 10 | 2/2 | 03-010-procurement-thresholds-and-methods-071526-0eb37f::003-e99d262956d2 (Oregon State University, 03-010 Procurement Thresholds and Methods 071526.pdf, p[3, 4], 5.1, None, 5.43) | ok |
| i-st-invoice | neutral | {} | 10 | 1/1 | 5-1-1-procurement-policies-administrative-guide-557216::003-b2b2e1ca9cba (Stanford University, 5.1.1 Procurement Policies _ Administrative Guide.pdf, p[3], 6, None, 8.60) | ok |
| i-pe-exception | neutral | {} | 10 | 1/1 | 2305-compliance-with-procurement-policies-ced954::002-800094d09b2b (University of Pennsylvania, 2305 Compliance with Procurement Policies – Division of Finance _ University of Pennsylvania.pdf, p[2, 3], 7, 1990-09, 7.94) | ok |
| i-ut-clause | neutral | {} | 10 | 1/1 | information-resources-use-and-security-policy-76b891::037-c46c35eec642 (The University of Texas at Austin, INFORMATION RESOURCES USE AND SECURITY POLICY _ UT Austin Information Security Office.pdf, p[37], None, None, 4.26) | ok |
| i-mi-mileage | point_in_time | {'uconn-travel-entertainment-procedures': ['2026-07-01-travel-and-entertainment-procedures-ca903b']} | 10 | 1/1 | travel-booking-procurement-services-university-74c4ef::003-854674a8d2df (University of Michigan, Travel Booking _ Procurement Services - University of Michigan.pdf, p[3], None, None, 10.09) | ok |
| i-ro-highrisk | neutral | {} | 10 | 1/1 | international-travel-policy-policies-procedures-f05fa2::006-e7cd0a6f9066 (University of Rochester, International Travel Policy - Policies & Procedures @ University of Rochester.pdf, p[6, 7], None, None, 7.37) | ok |
| i-ya-internet | neutral | {} | 10 | – | 5002-remote-work-policy-its-your-yale-full-text-28569a::002-dd0c64b72762 (Yale University, 5002 Remote Work Policy _ It’s Your Yale (full-text capture 2026-09-25).pdf, p[2], None, 2022-08-31, 3.92) | ok |
| i-st-mileage | neutral | {} | 10 | – | procedures-for-travel-and-other-expenses-3ded29::015-aa350b99fc63 (McGill University, procedures_for_travel_and_other_expenses_april172026_v6.9.pdf, p[15], PR7.4, 2026-05-01, 1.87) | ok |
| i-harvard-bids | neutral | {} | 10 | – | 2305-compliance-with-procurement-policies-ced954::002-800094d09b2b (University of Pennsylvania, 2305 Compliance with Procurement Policies – Division of Finance _ University of Pennsylvania.pdf, p[2, 3], 7, 1990-09, 3.50) | ok |
| i-ro-tips | neutral | {} | 10 | – | chapter-11-university-procurement-services-1dbc68::017-7d78efa70791 (Rutgers University, Chapter 11 University Procurement Services Procedures Manual.pdf, p[17, 18], 11.6.3, None, 1.85) | ok |
| i-uc-nyc-hotel | neutral | {} | 10 | 1/1 | travel-and-entertainment-policy-university-5f7c66::008-e92a8e596cb3 (University of Connecticut, Travel and Entertainment Policy _ University Policies _ University of Connecticut (capture 2026-09-25).pdf, p[8], None, 2026-07-01, -1.67) | ok |
| mi-uc-card | neutral | {} | 10 | – | 2026-07-01-travel-and-entertainment-procedures-ca903b::005-73722dbc7d99 (University of Connecticut, 2026-07-01-Travel-and-Entertainment-Procedures.pdf, p[5], None, 2026-07-01, 6.36) | ok |
| mi-penn | neutral | {} | 10 | – | 2305-compliance-with-procurement-policies-ced954::002-800094d09b2b (University of Pennsylvania, 2305 Compliance with Procurement Policies – Division of Finance _ University of Pennsylvania.pdf, p[2, 3], 7, 1990-09, 4.77) | ok |
| mi-mi-air | neutral | {} | 10 | – | travel-booking-procurement-services-university-74c4ef::008-322b78084217 (University of Michigan, Travel Booking _ Procurement Services - University of Michigan.pdf, p[8], None, None, 8.05) | ok |
| mi-or-emergency | neutral | {} | 10 | – | 03-010-procurement-thresholds-and-methods-071526-0eb37f::003-e99d262956d2 (Oregon State University, 03-010 Procurement Thresholds and Methods 071526.pdf, p[3, 4], 5.1, None, 3.19) | ok |
| mi-ut-ru | neutral | {} | 10 | – | chapter-11-university-procurement-services-1dbc68::017-7d78efa70791 (Rutgers University, Chapter 11 University Procurement Services Procedures Manual.pdf, p[17, 18], 11.6.3, None, -0.17) | ok |
| mi-st-ro | neutral | {} | 10 | – | 5-1-1-procurement-policies-administrative-guide-557216::003-b2b2e1ca9cba (Stanford University, 5.1.1 Procurement Policies _ Administrative Guide.pdf, p[3], 6, None, 4.35) | ok |
| mi-mi-ru-dated | point_in_time | {'uconn-travel-entertainment-procedures': ['2026-07-01-travel-and-entertainment-procedures-ca903b']} | 10 | – | travel-booking-procurement-services-university-74c4ef::003-854674a8d2df (University of Michigan, Travel Booking _ Procurement Services - University of Michigan.pdf, p[3], None, None, 7.30) | ok |
| mi-three | point_in_time | {'uconn-travel-entertainment-procedures': ['2026-07-01-travel-and-entertainment-procedures-ca903b']} | 10 | – | travel-booking-procurement-services-university-74c4ef::003-854674a8d2df (University of Michigan, Travel Booking _ Procurement Services - University of Michigan.pdf, p[3], None, None, 2.40) | ok |
| mi-uc-mixed-temporal | point_in_time | {'uconn-travel-entertainment-procedures': ['travel-and-entertainment-procedures-final-ccccf9']} | 10 | – | travel-and-entertainment-procedures-final-ccccf9::005-bb1bf7113880 (University of Connecticut, Travel-and-Entertainment-Procedures-FINAL.pdf, p[5], None, 2026-02-01, 3.29) | ok |
| mi-uc-jul-scope | point_in_time | {'uconn-travel-entertainment-procedures': ['2026-07-01-travel-and-entertainment-procedures-ca903b']} | 10 | – | 2026-07-01-travel-and-entertainment-procedures-ca903b::005-73722dbc7d99 (University of Connecticut, 2026-07-01-Travel-and-Entertainment-Procedures.pdf, p[5], None, 2026-07-01, 3.98) | ok |
| mi-ro-harvard | neutral | {} | 10 | – | international-travel-policy-policies-procedures-f05fa2::006-e7cd0a6f9066 (University of Rochester, International Travel Policy - Policies & Procedures @ University of Rochester.pdf, p[6, 7], None, None, 5.32) | ok |
| mi-ru-yale | neutral | {} | 10 | – | chapter-11-university-procurement-services-1dbc68::019-a3a75ea77ccd (Rutgers University, Chapter 11 University Procurement Services Procedures Manual.pdf, p[19, 20], 11.7.8, None, 3.03) | ok |
| mi-ctl-single | neutral | {} | 10 | – | international-travel-policy-policies-procedures-f05fa2::005-d0d3faeda922 (University of Rochester, International Travel Policy - Policies & Procedures @ University of Rochester.pdf, p[5], None, None, 1.20) | ok |
| mi-ctl-explain | neutral | {} | 10 | – | 5-1-1-procurement-policies-administrative-guide-557216::003-b2b2e1ca9cba (Stanford University, 5.1.1 Procurement Policies _ Administrative Guide.pdf, p[3], 6, None, 3.61) | ok |
| mi-ctl-refers | neutral | {} | 10 | – | information-resources-use-and-security-policy-76b891::037-c46c35eec642 (The University of Texas at Austin, INFORMATION RESOURCES USE AND SECURITY POLICY _ UT Austin Information Security Office.pdf, p[37], None, None, 4.26) | ok |
| mi-ctl-compare | compare | {'uconn-travel-entertainment-procedures': ['2026-07-01-travel-and-entertainment-procedures-ca903b', 'travel-and-entertainment-procedures-final-ccccf9']} | 10 | – | 2026-07-01-travel-and-entertainment-procedures-ca903b::001-b18d26ad0b00 (University of Connecticut, 2026-07-01-Travel-and-Entertainment-Procedures.pdf, p[1], None, 2026-07-01, 1.75) | ok |

### remote: 43/43

| case | checks |
|---|---|
| i-uc-jul-submit | ok |
| i-uc-feb-suspend | ok |
| i-uc-jul-owner | ok |
| i-uc-cur-reinstate | ok |
| i-uc-cur-multibed | ok |
| i-uc-cmp-card | ok |
| i-uc-neutral-card | ok |
| i-uc-cmp-lodging | ok |
| i-uc-march-tickets | ok |
| i-uc-feb-reinstate | ok |
| i-uc-unavailable | ok |
| i-uc-neu-sio | ok |
| i-uc-spouse | ok |
| i-uc-personal-leg | ok |
| i-ru-tips | ok |
| i-ru-advance | ok |
| i-or-180k | ok |
| i-st-invoice | ok |
| i-pe-exception | ok |
| i-ut-clause | ok |
| i-mi-mileage | ok |
| i-ro-highrisk | ok |
| i-ya-internet | ok |
| i-st-mileage | ok |
| i-harvard-bids | ok |
| i-ro-tips | ok |
| i-uc-nyc-hotel | ok |
| mi-uc-card | ok |
| mi-penn | ok |
| mi-mi-air | ok |
| mi-or-emergency | ok |
| mi-ut-ru | ok |
| mi-st-ro | ok |
| mi-mi-ru-dated | ok |
| mi-three | ok |
| mi-uc-mixed-temporal | ok |
| mi-uc-jul-scope | ok |
| mi-ro-harvard | ok |
| mi-ru-yale | ok |
| mi-ctl-single | ok |
| mi-ctl-explain | ok |
| mi-ctl-refers | ok |
| mi-ctl-compare | ok |

### filters: 10/10

| case | arguments | candidates | filtered out | results (chunk, retrieval rank) | checks |
|---|---|---|---|---|---|
| org-short-name | {'organization': 'UConn', 'limit': 10} | 20 | 3 | 005-73722dbc7d99@1, 005-bb1bf7113880@2, 005-7b9397f6ba84@3, 005-a7d23de3a5c6@4, 005-211ea5ecba99@5, 005-cb1a5edd163e@7, 007-5a42d416ec2b@10, 001-582b64b74372@11, 001-8d1922a01072@12, 001-b18d26ad0b00@13 | ok |
| current-only | {'current_only': True, 'limit': 20} | 20 | 6 | 005-73722dbc7d99@1, 005-a7d23de3a5c6@4, 005-211ea5ecba99@5, 019-a3a75ea77ccd@6, 005-cb1a5edd163e@7, 005-d3693594d7b6@8, 019-779fa7091a27@9, 007-5a42d416ec2b@10, 001-8d1922a01072@12, 001-b18d26ad0b00@13, 004-af625b230e93@15, 004-c95fa8d3fe32@16, 004-ecee129829b6@18, 006-186f24f39403@20 | ok |
| as-of-march | {'as_of': '2026-03-15', 'limit': 20} | 20 | 11 | 005-bb1bf7113880@2, 005-7b9397f6ba84@3, 019-a3a75ea77ccd@6, 005-d3693594d7b6@8, 019-779fa7091a27@9, 001-582b64b74372@11, 001-fc82968e5020@14, 004-d832d7afe8a6@17, 006-dfec256b5b28@19 | ok |
| as-of-march-other-orgs | {'as_of': '2026-03-15', 'limit': 20} | 20 | 4 | 002-800094d09b2b@1, 003-b68f37d5a941@2, 005-9a54aa8be9f8@3, 004-50c70a951d99@4, 003-e99d262956d2@5, 004-c3dcf461e498@6, 003-b2b2e1ca9cba@7, 002-dab79191d715@8, 002-0a557974318a@9, 005-e9921c2802e4@10, 001-7a470922f1bf@11, 007-4909c046f6a3@12, 010-1ff3fe0f464f@13, 008-487f08420e50@15, 012-cda1f6d6d46a@16, 018-1dbccb35ef43@17 | ok |
| as-of-august | {'as_of': '2026-08-01', 'limit': 20} | 20 | 6 | 005-73722dbc7d99@1, 005-a7d23de3a5c6@4, 005-211ea5ecba99@5, 019-a3a75ea77ccd@6, 005-cb1a5edd163e@7, 005-d3693594d7b6@8, 019-779fa7091a27@9, 007-5a42d416ec2b@10, 001-8d1922a01072@12, 001-b18d26ad0b00@13, 004-af625b230e93@15, 004-c95fa8d3fe32@16, 004-ecee129829b6@18, 006-186f24f39403@20 | ok |
| doc-id | {'doc_id': 'travel-and-entertainment-procedures-final-ccccf9', 'limit': 20} | 20 | 14 | 005-bb1bf7113880@2, 005-7b9397f6ba84@3, 001-582b64b74372@11, 001-fc82968e5020@14, 004-d832d7afe8a6@17, 006-dfec256b5b28@19 | ok |
| superseded-doc-current-only-empty | {'doc_id': 'travel-and-entertainment-procedures-final-ccccf9', 'current_only': True} | 20 | 20 | (none) | ok |
| other-org-not-in-pool | {'organization': 'Yale'} | 20 | 20 | (none) | ok |
| rutgers-question-rutgers-filter | {'organization': 'Rutgers University', 'limit': 5} | 20 | 7 | 019-a3a75ea77ccd@3, 007-37315e9be280@4, 004-eab1021a5168@5, 020-e24805cff174@6, 001-fd57fb4529df@7 | ok |
| ranks-keep-retrieval-order | {'organization': 'UConn', 'current_only': True, 'limit': 20} | 20 | 9 | 005-73722dbc7d99@1, 005-a7d23de3a5c6@4, 005-211ea5ecba99@5, 005-cb1a5edd163e@7, 007-5a42d416ec2b@10, 001-8d1922a01072@12, 001-b18d26ad0b00@13, 004-af625b230e93@15, 004-c95fa8d3fe32@16, 004-ecee129829b6@18, 006-186f24f39403@20 | ok |

### metadata: 12/12

| case | checks |
|---|---|
| all-documents | ok |
| organization Oregon State | ok |
| organization Penn | ok |
| organization Rutgers | ok |
| organization Stanford | ok |
| organization UConn | ok |
| organization UT Austin | ok |
| organization Yale | ok |
| uconn-series | ok |
| uconn-current | ok |
| chunk-lookup | ok |
| empty-combination | ok |

### rejections: 13/13

| case | tool | expected | got | checks |
|---|---|---|---|---|
| search-missing-query | document_search | invalid_arguments | invalid_arguments | ok |
| search-extra-field | document_search | invalid_arguments | invalid_arguments | ok |
| search-limit-too-big | document_search | invalid_arguments | invalid_arguments | ok |
| search-bad-date | document_search | invalid_arguments | invalid_arguments | ok |
| search-both-version-filters | document_search | invalid_arguments | invalid_arguments | ok |
| search-unknown-org | document_search | unknown_organization | unknown_organization | ok |
| search-unknown-doc | document_search | unknown_document | unknown_document | ok |
| lookup-unknown-chunk | metadata_lookup | not_found | not_found | ok |
| lookup-chunk-and-filter | metadata_lookup | invalid_arguments | invalid_arguments | ok |
| db-sql-text | database_query | invalid_arguments | invalid_arguments | ok |
| db-missing-parameter | database_query | invalid_arguments | invalid_arguments | ok |
| db-not-configured | database_query | database_not_configured | database_not_configured | ok |
| unknown-tool | shell_exec | -32602 | -32602 | ok |
