# Phase 9 evaluation (PostgreSQL behind database_query; no language model)

Database: postgresql://adaptiverag:***@127.0.0.1:5433/adaptiverag (PostgreSQL 16.15 (Debian 16.15-1.pgdg13+2)), schema `adaptiverag_eval` rebuilt for the run. Every query through the MCP protocol (mcp.Client → services.mcp.server → PostgresDatabase); expectations from the repository metadata (MetadataCatalog over data/). See evaluation/database/run.py.

## 55/55 checks passed

### schema: 6/6

| case | detail | checks |
|---|---|---|
| first-migrate |  | ok |
| idempotent |  | ok |
| recorded-checksums |  | ok |
| tables | tables=['chunks', 'corpus_loads', 'documents', 'schema_migrations'] | ok |
| indexes | indexes=['chunks_doc_id_ordinal_key', 'chunks_pkey', 'corpus_loads_pkey', 'documents_document_type', 'documents_domain', 'documents_is_current', 'documents_orga | ok |
| changed-migration-refused |  | ok |

### load: 3/3

| case | detail | checks |
|---|---|---|
| counts | documents=12, chunks=466 | ok |
| reload-identical | corpus_loads=2 | ok |
| manifest-hashes |  | ok |

### queries: 38/38

| case | detail | checks |
|---|---|---|
| documents-all | rows=12 | ok |
| documents-equal-metadata_lookup |  | ok |
| documents organization=McGill University | rows=1 | ok |
| documents organization=Oregon State University | rows=1 | ok |
| documents organization=Rutgers University | rows=1 | ok |
| documents organization=Stanford University | rows=1 | ok |
| documents organization=The University of Texas at Austin | rows=1 | ok |
| documents organization=University of Connecticut | rows=3 | ok |
| documents organization=University of Michigan | rows=1 | ok |
| documents organization=University of Pennsylvania | rows=1 | ok |
| documents organization=University of Rochester | rows=1 | ok |
| documents organization=Yale University | rows=1 | ok |
| documents domain=information_security | rows=1 | ok |
| documents domain=procurement | rows=3 | ok |
| documents domain=remote_work | rows=1 | ok |
| documents domain=travel_expenses | rows=7 | ok |
| documents document_type=guidance | rows=1 | ok |
| documents document_type=policy | rows=6 | ok |
| documents document_type=procedure | rows=4 | ok |
| documents document_type=standard | rows=1 | ok |
| documents is_current=True | rows=4 | ok |
| documents is_current=False | rows=1 | ok |
| document_versions uconn-travel-entertainment-procedures | versions=[('travel-and-entertainment-procedures-final-ccccf9', '2026-02-01', False), ('2026-07-01-travel-and-entertainment-procedures-ca903b', '2026-07-01', Tru | ok |
| document_versions unknown |  | ok |
| chunks_by_document 03-010-procurement-thresholds-and-methods-071526-0eb37f | rows=40 | ok |
| chunks_by_document 2026-07-01-travel-and-entertainment-procedures-ca903b | rows=24 | ok |
| chunks_by_document 2305-compliance-with-procurement-policies-ced954 | rows=4 | ok |
| chunks_by_document 5-1-1-procurement-policies-administrative-guide-557216 | rows=26 | ok |
| chunks_by_document 5002-remote-work-policy-its-your-yale-full-text-28569a | rows=11 | ok |
| chunks_by_document chapter-11-university-procurement-services-1dbc68 | rows=62 | ok |
| chunks_by_document information-resources-use-and-security-policy-76b891 | rows=154 | ok |
| chunks_by_document international-travel-policy-policies-procedures-f05fa2 | rows=18 | ok |
| chunks_by_document procedures-for-travel-and-other-expenses-3ded29 | rows=55 | ok |
| chunks_by_document travel-and-entertainment-policy-university-5f7c66 | rows=35 | ok |
| chunks_by_document travel-and-entertainment-procedures-final-ccccf9 | rows=24 | ok |
| chunks_by_document travel-booking-procurement-services-university-74c4ef | rows=13 | ok |
| truncation |  | ok |
| hostile-value |  | ok |

### failures: 8/8

| case | detail | checks |
|---|---|---|
| no-database-url | expected=database_not_configured, got=database_not_configured | ok |
| closed-port | expected=database_unavailable, got=database_unavailable | ok |
| unmigrated-schema | expected=database_unavailable, got=database_unavailable | ok |
| unloaded-schema | expected=database_unavailable, got=database_unavailable | ok |
| mistyped-parameter | expected=invalid_arguments, got=invalid_arguments | ok |
| unknown-parameter | expected=invalid_arguments, got=invalid_arguments | ok |
| sql-text | expected=invalid_arguments, got=invalid_arguments | ok |
| missing-required | expected=invalid_arguments, got=invalid_arguments | ok |
