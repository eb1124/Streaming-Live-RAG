# AdaptiveRAG phase 9: PostgreSQL persistence for database_query

Phase 8 defined the `database_query` MCP tool: named, parameterized metadata queries, never SQL text. It had no
database (`NotConfigured`). Phase 9 adds PostgreSQL behind that contract: a schema with migrations, a reproducible
bootstrap load of the repository metadata, and an adapter. Nothing else changes. Retrieval, generation,
`document_search`, `metadata_lookup`, the Redis sessions and the RabbitMQ jobs work exactly as before.

```
MCP client ── database_query {query_name, parameters, limit} ──► services.mcp.server (validation, phase 8)
                                                                   │ Tools.database_query
                                                                   ▼ DatabaseAdapter (services/mcp/database.py)
                                        DATABASE_URL unset ──► NotConfigured        → database_not_configured
                                        DATABASE_URL set   ──► PostgresDatabase     (services/mcp/postgres.py)
                                                                   │ MetadataRepository (services/persistence/repository.py):
                                                                   │ fixed statement + bound parameters (queries.py)
                                                                   ▼
                                                     PostgreSQL schema "adaptiverag": documents, chunks, corpus_loads
                                                                   ▲
                       python -m services.persistence load ────────┘  copied from data/ingested + data/chunks
```

## What PostgreSQL holds, and what it does not

| store | holds | source of truth |
|---|---|---|
| PostgreSQL (phase 9) | document, version and chunk metadata for `database_query`; a record of every load | **no**: a copy of `data/`, rebuilt by `load` |
| `data/` files | ingestion output, chunks, indexes | yes. Retrieval and `metadata_lookup` read them, unchanged |
| Redis (phase 5) | conversation sessions | yes. Unchanged, not copied to PostgreSQL |
| RabbitMQ (phase 4) | job and event queues | unchanged |

Not stored: chunk text and retrieval text (they stay in `data/chunks`), excluded chunk records, embeddings, sessions,
job state.

## Schema (`services/persistence/migrations/0001_initial.sql`)

| table | key | columns | relationships / indexes |
|---|---|---|---|
| `schema_migrations` | `version` | `name`, `checksum` (SHA-256 of the file), `applied_at` | made by the migration runner |
| `corpus_loads` | `load_id` (identity) | `loaded_at`, `ingestion_version`, `chunker_version`, SHA-256 of both `_manifest.json` files, `documents`, `chunks` | one row per load |
| `documents` | `doc_id` | `load_id`, `filename`, `status`, `title`, `organization`, `domain`, `document_type`, `authority_level`, `version`, `effective_date` / `effective_on` (date, only for a full ISO date), `effective_date_text`, `revision_date`, `issued_date`, `superseded_date` / `superseded_on`, `is_current`, `series_id`, `source_url`, `capture_date`, `page_count`, `file_sha256`, `content_hash`, `chunk_count`, `excluded_records`, `missing_metadata` (jsonb), `metadata_provenance` (jsonb) | FK `load_id` → `corpus_loads`. Indexes: `organization`, `(series_id, effective_on)`, `domain`, `document_type`, `is_current` |
| `chunks` | `chunk_id` | `doc_id`, `ordinal`, `page_start`, `page_end`, `pages` (int[]), `section_path` (text[]), `clause_id`, `clause_ids` (text[]), `split`, `token_count`, `confidence`, `content_hash`, `prev_chunk_id`, `next_chunk_id` | FK `doc_id` → `documents` (ON DELETE CASCADE); unique `(doc_id, ordinal)` |

The values are the ones `metadata_lookup` reports (`MetadataCatalog`). Dates are kept as the chunks carry them; the
typed `effective_on` and `superseded_on` are set only for full ISO dates (the rule of `temporal/versions.py`).

**Migrations** (`services/persistence/migrate.py`): numbered plain-SQL files `NNNN_name.sql`, versions 1..n without
gaps. `migrate`:
1. creates the schema and `schema_migrations` if needed;
2. takes an advisory lock;
3. refuses to run if an applied migration's file changed (checksum) or disappeared;
4. applies each pending file in its own transaction.

Running it again applies nothing. Never edit an applied file: add `0002_...sql`. No ORM and no migration framework:
three tables and fixed queries do not need one.

**Load** (`services/persistence/load.py`): one transaction that records a `corpus_loads` row, deletes all documents
and chunks, inserts the catalog's (12 documents, 466 chunks today), and checks the counts. Loading the same files
again gives identical rows. Re-run it after re-ingesting or re-chunking.

## database_query

The phase 8 contract (`services/mcp/contracts.py`): `query_name`, `parameters`, `limit` (1-500), correlation ids.
Each name has exactly one constant statement (`services/persistence/queries.py`). Parameters are always bound
(`%(name)s`). An absent optional filter is NULL and matches everything. Text keys sort `COLLATE "C"` (byte order,
the same on every server), so a database state always gives the same rows in the same order.

| query | parameters | columns | order |
|---|---|---|---|
| `documents` | `organization`, `domain`, `document_type` (str), `is_current` (bool); all optional, exact match | doc_id, title, organization, domain, document_type, authority_level, version, effective_date, superseded_date, is_current, series_id, filename, page_count, chunk_count | doc_id |
| `document_versions` | `series_id` (str, required) | doc_id, series_id, title, organization, effective_date, superseded_date, is_current, filename | effective date (missing last), doc_id |
| `chunks_by_document` | `doc_id` (str, required) | chunk_id, doc_id, ordinal, page_start, page_end, pages, section_path, clause_id, clause_ids, split, token_count, confidence, content_hash | ordinal |

Output: `{source: "postgresql", columns, rows: [{column: value}], row_count, truncated, correlation}`. The adapter
fetches `limit + 1` rows to set `truncated`. An empty `rows` list is a valid result (e.g. a filter nothing matches).
`organization` must be the full corpus name: short names such as "UConn" are a `metadata_lookup` convenience.

Layers: the MCP tool validates (phase 8 contract) → `PostgresDatabase` (`services/mcp/postgres.py`, the only MCP
code that knows PostgreSQL exists) → `MetadataRepository` (`services/persistence/`, which knows nothing of MCP; the
only package that imports `psycopg`) → PostgreSQL.

Per call the repository does the following:
1. opens one connection (`DATABASE_CONNECT_TIMEOUT_S`, `statement_timeout` = `DATABASE_STATEMENT_TIMEOUT_MS`,
   `search_path` = `DATABASE_SCHEMA`);
2. runs a read-only transaction: checks that a corpus is loaded, runs the statement;
3. closes the connection.

There is no pool, and nothing is retried.

Phase 10A: the call is one tracing span, `db.query` (`db.system.name` = `postgresql`, the query's name, the schema,
the row count, `truncated`; on a failure the error class). The SQL, the parameters, the rows and `DATABASE_URL` are
never recorded. Connections, transactions and results are unchanged. See [telemetry.md](telemetry.md).

**Errors** (`isError` results, as in phase 8):

| situation | code | message |
|---|---|---|
| `DATABASE_URL` not set | `database_not_configured` | "no database adapter is configured (set DATABASE_URL; ...)" (phase 8 behaviour) |
| server unreachable, wrong credentials | `database_unavailable` | "cannot connect to postgresql://user:***@host:port/db: ..." (password never shown) |
| schema not migrated | `database_unavailable` | "schema 'adaptiverag' is not initialized: run python -m services.persistence migrate, then load" |
| migrated but nothing loaded | `database_unavailable` | "no corpus is loaded in schema 'adaptiverag': run python -m services.persistence load" |
| statement failed (e.g. its timeout) | `database_unavailable` | "<query_name> failed: <first line of the server error>" |
| unknown query or parameter, missing required parameter, wrong parameter type (e.g. `is_current: "true"`), SQL text | `invalid_arguments` | per-field details. The database is never contacted. |

Phase 9 added to the phase 8 contract: the code `database_unavailable` (an unreachable configured database used
to have no code of its own) and type checks on parameter values (`is_current` must be a boolean, the others
strings). The tool's input and output JSON schemas are unchanged.

## Configuration (`services/persistence/config.py`)

| variable | default | |
|---|---|---|
| `DATABASE_URL` | unset → no database | libpq URL; credentials only here (`.env` or the shell), never in git |
| `DATABASE_SCHEMA` | `adaptiverag` | `[a-z_][a-z0-9_]*` |
| `DATABASE_CONNECT_TIMEOUT_S` | `5` | |
| `DATABASE_STATEMENT_TIMEOUT_MS` | `5000` | |
| `ADAPTIVERAG_TEST_DATABASE_URL` | unset → live tests skipped | for `pytest -m postgres` only; the tests use and drop their own random schemas |

## Local PostgreSQL (Docker), PowerShell

```
pip install -e ".[postgres,mcp,services]"
docker run -d --name adaptive-postgres -p 5433:5432 -e POSTGRES_USER=adaptiverag -e POSTGRES_PASSWORD=adaptiverag -e POSTGRES_DB=adaptiverag postgres:16
$env:DATABASE_URL = "postgresql://adaptiverag:adaptiverag@127.0.0.1:5433/adaptiverag"   # development-only password
python -m services.persistence migrate
python -m services.persistence load
python -m services.persistence status
python -m services.mcp                      # database_query now served by PostgreSQL
'{"query_name":"document_versions","parameters":{"series_id":"uconn-travel-entertainment-procedures"}}' | Set-Content -Encoding utf8 q.json
python -m services.mcp.cli call database_query --args-file q.json   # PowerShell 5.1 strips quotes from -j JSON
```

`python -m services.mcp.cli --stdio ...` passes `DATABASE_*` (and `MCP_*`, `RETRIEVAL_*`, `SERVICE_TIMEOUT_S`) on
to the server process it starts. The MCP SDK starts stdio servers with a minimal environment.

Host port 5433 avoids clashing with a PostgreSQL installed on the machine (5432). `docker stop adaptive-postgres`
/ `docker start adaptive-postgres` keep the data (in the container). `docker rm -f adaptive-postgres` deletes it;
then run `migrate` and `load` again. No compose file: one container, the same way phase 7 starts RabbitMQ and Redis.

## Tests and evaluation

* `tests/test_persistence.py`: 28 offline tests, always run, with a fake psycopg connection:
  * configuration and wiring;
  * one constant statement per query;
  * values only as bound parameters;
  * read-only connection with timeouts, closed after use;
  * structured rows, truncation, empty results;
  * every `database_unavailable` path, a real closed local port, and 9 invalid-argument cases that never connect;
  * migration files and checksum refusal;
  * load rows equal to what `metadata_lookup` reports (synthetic and real `data/`);
  * layering: `psycopg` only in `services/persistence`, which imports nothing from MCP, and neither the frozen
    core nor the phase 7 services use it.

  Plus 4 live tests (`-m postgres`, need `ADAPTIVERAG_TEST_DATABASE_URL`):
  * migrations once, with checksum, tables, indexes and timeout, and refusal of a changed migration;
  * a load equal to the catalog that reloads identically;
  * every query through MCP;
  * unmigrated and unloaded schemas.
* `python -m evaluation.database.run` (`evaluation/database/results/postgres.md`). Needs `DATABASE_URL` and uses its
  own schema `adaptiverag_eval`, rebuilt every run. It checks schema, load, every named query for every organization,
  domain, type, series and document against the repository metadata (and against `metadata_lookup`), and failure
  cases. Result 2026-10-02: 55/55 on PostgreSQL 16.

## Limitations

* PostgreSQL is a copy of `data/`. After re-ingesting or re-chunking, `load` must be run again; nothing detects a
  stale copy automatically. `corpus_loads` records the manifest hashes, so it can be checked by hand.
* Only the three phase 8 queries exist. There is no write path through MCP and no free-form query.
* One connection per call, no pool and no retry: fine for interactive tool calls, not for high request rates.
* Sessions stay in Redis and job state is still not persisted. The worker's record of finished jobs is still only
  in memory (`docs/session_store.md`); fixing that would change `adaptive/jobs`, outside this phase.
* No database roles or grants are managed. The application user owns the schema. Use a read-only role for the MCP
  server in a shared deployment.
