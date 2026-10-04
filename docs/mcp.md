# AdaptiveRAG phase 8: MCP tool layer

An MCP server (`services/mcp/`, the official `mcp` Python SDK 2.2, low-level `Server`) with three tools. It is an
additional entry point beside the phase 7 api. It reuses the phase 7 retrieval service and the repository metadata,
has no retrieval or index of its own, and changes nothing in phases 1 to 7.

```
MCP client (any; services.mcp.cli for manual tests)
  │  Streamable HTTP  http://127.0.0.1:8003/mcp     or  stdio (the client starts the process)
  ▼
services.mcp.server  ── tools/list, tools/call (schemas = services/mcp/contracts.py)
  ├─ document_search ──► SearchBackend
  │                        RemoteSearch: POST /retrieve on the phase 7 retrieval service (:8001)   default
  │                        LocalSearch:  phase 7 RetrievalService in-process (loads the stack)     --retrieval local
  │                      = frozen generation.pipeline.retrieve, or the phase 6 loop (mode=iterative)
  ├─ metadata_lookup ──► MetadataCatalog: data/ingested/_manifest.json, data/chunks, organization_aliases.toml,
  │                      temporal.versions.VersionRegistry
  └─ database_query  ──► DatabaseAdapter: NotConfigured, or PostgreSQL with DATABASE_URL (phase 9, docs/database.md)
```

Not implemented: React, MCP resources and prompts, authentication. OpenTelemetry tracing: phase 10A, see Tracing
below and [telemetry.md](telemetry.md).

## Tools

All three are read-only (`readOnlyHint: true`). Every input accepts optional `request_id`, `job_id` and
`session_id` (`[A-Za-z0-9_.:-]`, ≤128). Unknown fields are rejected (`additionalProperties: false`). Every output
carries `schema_version` and `correlation`.

| tool | input | output |
|---|---|---|
| `document_search` | `query` (required, ≤4000); `limit` 1-20 (5); `mode` `single` \| `iterative`; `max_rounds` 1-10 (3); filters `organization` (full name or short name), `doc_id`, `current_only`, `as_of` (ISO date; not with `current_only`) | `results[]`: `rank`, `chunk_id`, `document` (doc_id, title, **filename**, organization, domain, type, authority, version, effective / superseded date, is_current, series_id, source_url, page_count), `location` (pages, section path, clause, source blocks), `score` (retrieval rank, value, retriever, rerank score), verbatim `text`, `content_hash`, `as_of_applicability` (with `as_of` only); `temporal` (the frozen resolver's reading of the query text, see below: kind, trigger, dates, selected versions, dropped); `candidates`, `filtered_out`, `filters`, `backend`, `rounds`, `notes` |
| `metadata_lookup` | `chunk_id` (one chunk and its document), or filters `doc_id`, `organization`, `series_id`, `domain`, `document_type`, `current_only`; `include_provenance`, `include_text` | `documents[]` (manifest + chunk metadata: filename, status, title, organization, dates normalized and as printed, is_current, series and its versions, source, page count, file hash, chunk / excluded counts, missing metadata, per-field provenance); `chunk`; `corpus` (counts, organizations, pipeline versions); `notes` |
| `database_query` | `query_name` `documents` \| `document_versions` \| `chunks_by_document`; `parameters` (checked per query); `limit` 1-500. Never SQL text | `columns`, `rows`, `row_count`, `truncated`, `source`. Without `DATABASE_URL`: the error `database_not_configured`; with it, PostgreSQL (phase 9, [database.md](database.md)) |

**document_search filters** select among the chunks the retrieval returned and keep its order (`retrieval_rank`).
The tool always asks for the whole reranked pool (k = 20 = `CANDIDATE_POOL`), filters it, and returns the first
`limit`. Filters do not widen the search: an organization the query does not lead the retrieval to can give an empty
result (`notes` says so). `current_only` drops documents whose `is_current` is false. Without filters the results
are the frozen retrieval's top `limit`, chunk for chunk.

**Two different temporal mechanisms.** Do not confuse them:

| | `structured_content.temporal` | the `as_of` argument |
|---|---|---|
| what | the frozen retrieval's temporal resolver (`temporal.resolve`) reading the **query text**: dates and words such as "currently" or "February 2026" | an explicit **metadata filter** of the MCP tool (`MetadataCatalog.applicability`), applied to the retrieved candidates |
| where it acts | inside retrieval, before reranking: it drops unselected versions of a series | after retrieval: it removes candidates, never adds any |
| shown in | `temporal` (kind, trigger, dates, selected, dropped) | `filters.as_of`, `filtered_out`, each result's `as_of_applicability`, `notes` |

`as_of` is never sent to the retrieval service and does not change `temporal`. A query without date words reports
`temporal.kind = neutral` even with `as_of` set. Both can apply at once: "current" in the query and an earlier
`as_of` together leave no version of a series (an empty result, with a note).

**`as_of` rules** (from chunk metadata only; a "full" date is an ISO `YYYY-MM-DD`; month-only values such as `1990-09`
or `2026-03` are not usable, the same rule as `temporal/versions.py`):

| document | kept when | `as_of_applicability` |
|---|---|---|
| a version of a multi-version series (`series_id`, e.g. the two UConn procedures) | it is the version in force that day (`VersionRegistry.in_force`, unchanged) | `in_force` |
| standalone, full effective date | effective date ≤ `as_of`, and no full superseded date ≤ `as_of` | `in_force` |
| standalone, no usable effective date | always (unless a full superseded date ≤ `as_of`) | `undetermined` |

Undetermined documents stay eligible because their metadata cannot show they did not apply. They are not
verified: a note names them (`as_of <date>: applicability could not be established from metadata for N returned
document(s) ...`). In the current corpus these are Oregon State, Stanford, Rutgers, UT Austin, Rochester, Michigan
(no effective date) and Penn (`1990-09`). Excluded: the UConn Travel and Entertainment Policy before 2026-07-01, the
McGill procedures before 2026-05-01, and the non-applicable UConn procedures version.

**Errors.** A tool error is an `isError` result whose `structuredContent` is `{"error": {code, message, details,
correlation}}` (the same JSON is in the text content):

| code | when |
|---|---|
| `invalid_arguments` | schema violation; `details` = per-field `loc`, `type`, `msg`. Nothing runs. |
| `unknown_organization`, `unknown_document` | a filter names something not in the corpus; `details` lists the valid values |
| `not_found` | `chunk_id` is not a retrievable chunk |
| `backend_unavailable` | the retrieval service failed (transport, HTTP status, invalid response, mismatched correlation ids) |
| `database_not_configured` | database_query without `DATABASE_URL` |
| `database_unavailable` | database_query: the configured PostgreSQL cannot answer (unreachable, not migrated, nothing loaded, statement failed; phase 9) |
| `internal_error` | anything else; the exception stays in the server log |

An unknown tool name is a JSON-RPC protocol error `-32602 Unknown tool: <name>` (data: the tool names).

**Correlation.** The server makes `request_id` (`mcp-<16 hex>`) when none is given. It binds the ids while the tool
runs (`services.correlation`), so a remote `RetrievalRequest` carries them and `services.http.post` refuses a response
that does not echo them. The retrieval service logs `job` and `session`. Nothing is carried from one call to the next.

## Tracing (phase 10A)

Every call of one of the three tools is an OpenTelemetry span, `mcp.tool.execute`, and what the tool calls is its
child:

```
mcp.tool.execute                     mcp.tool.name, correlation ids; on an isError result: mcp.error.code, ERROR
├── POST /retrieve                   document_search, remote backend: the retrieval service's span, same trace
│   └── retrieval.execute
│       └── retrieval.round ...      mode=iterative
├── retrieval.execute                document_search, local backend
└── db.query                         database_query with PostgreSQL: db.system.name=postgresql, db.query.name,
                                     db.namespace, db.response.returned_rows, db.query.truncated
```

* **Tool results are unchanged.** Every result and every error code above is what phase 8 and 9 return. An
  `isError` result sets the span's status to `ERROR` with `mcp.error.code` (and `error.type`, the exception's
  type); an empty result is not an error. An unknown tool name is a protocol error and has no span.
* **Correlation.** The span carries the call's `request_id` (given or made by the server), `job_id` and
  `session_id`, and `correlation_id` (the job id when given, else the request id). The same ids are on the
  retrieval and database spans. They remain the application's correlation mechanism; the trace id is the tracer's
  own and nothing reads it. The remote backend sends the W3C `traceparent` header with `POST /retrieve`.
* **Never recorded:** the search query, tool arguments other than the tool name, result text, chunk ids, document
  names, the SQL statement, query parameters, rows, `DATABASE_URL`, exception messages. `db.query.name` is the
  contract's query name (`documents`, `document_versions`, `chunks_by_document`).
* **Without a collector** (the default) the spans are no-ops. `OTEL_TRACES_EXPORTER=console` prints them to stderr
  (stdout is the stdio transport's channel); `OTEL_EXPORTER_OTLP_ENDPOINT` exports OTLP over HTTP. The process's
  `service.name` is `adaptiverag-mcp`. `services.mcp.cli --stdio` passes the `OTEL_*` variables on.
* **Tests:** `tests/test_telemetry.py`, with the SDK's in-memory exporter: a span per tool call, every error code,
  the remote search in one trace, the database span under its tool call, no payload in any span, and tool results
  identical with tracing broken.

## Running

```
pip install -e ".[retrieval,services,mcp]"
python -m services.retrieval                                      # :8001, loads the stack
python -m services.mcp                                            # :8003/mcp, remote retrieval; GET /health
python -m services.mcp --transport stdio                          # for MCP clients that start the server
python -m services.mcp --retrieval local                          # load the stack here instead of :8001
python -m services.mcp.cli list | call <tool> -a key=value -j key=<json> [--args-file f.json] [--summary] [--stdio]
```

Environment: `MCP_HOST`, `MCP_PORT` (8003), `MCP_RETRIEVAL` (`remote`), `RETRIEVAL_URL`, `SERVICE_TIMEOUT_S`; tracing:
`OTEL_TRACES_EXPORTER`, `OTEL_EXPORTER_OTLP_ENDPOINT` (off by default).

Manual `as_of` check (PowerShell). On 2026-03-15 the February UConn procedures apply and the July version does not.
The UConn Policy (effective 2026-07-01) is excluded too. Other UConn documents can only appear if their metadata
allows it. Undated documents may appear, flagged `undetermined`:

```
$r = python -m services.mcp.cli call document_search -a "query=When is a UConn University Travel Card suspended?" -a as_of=2026-03-15 -j limit=5 | ConvertFrom-Json
$r.structured_content.results | ForEach-Object { "{0} | {1} | effective {2} | {3}" -f $_.rank, $_.document.doc_id, $_.document.effective_date, $_.as_of_applicability }
$r.structured_content.filters; $r.structured_content.filtered_out; $r.structured_content.notes
$r.structured_content.temporal      # neutral: the query text has no date; as_of is not part of it
```

## Files

`services/mcp/`: `contracts.py` (tool schemas), `catalog.py` (metadata), `backends.py` (local / remote search),
`database.py` (adapter contract), `tools.py` (tool logic), `server.py` (MCP server), `__main__.py` (process),
`cli.py` (client). Changed outside it: `pyproject.toml` (the `mcp` extra) and one line in `docs/services.md`.

## Tests and evaluation

* `tests/test_mcp.py` (55 tests, offline). Discovery and schemas; search equal to the frozen retrieval; provenance;
  temporal resolution; iterative mode; organization, current and as-of filters (series versions; future-dated, superseded and undated standalone documents); empty results; unknown names; metadata
  listing, filters, provenance and chunk lookup; 20 malformed-argument cases; unknown tool; crashes hide their text;
  the database contract with and without an adapter; correlation through to the retrieval service; the remote backend
  equals the local one; retrieval-service failures; frozen core and phase 7 do not import MCP; the tools do not alter
  core state; no network and no model calls; the real catalog against `data/`; the real server process over stdio.
* `python -m evaluation.mcp.run` (`evaluation/mcp/results/offline.md`). Real stack, through the MCP protocol:
  43 questions (27 integration + 16 phase 2) equal to the frozen retrieval with correct provenance; the same 43
  through the phase 7 retrieval service; 10 filter cases (as-of cases: no result with a full effective date after the date, undated results flagged and noted); 12 metadata checks; 13 rejections.

## Limitations

* Filters apply to the 20 retrieved candidates and do not search further (see above).
* `as_of` can only exclude what the metadata dates: documents without a full effective date pass unverified (flagged `undetermined`).
* `iterative` mode runs the phase 6 loop with k = 20. The orchestrator uses k = 10, so iterative results can differ
  from what the orchestrator's loop shows.
* The MCP server reads `data/` itself for metadata. It must be the data the retrieval service indexed. Remote results
  carry the retrieval service's own chunk copies, and only `filename` / `page_count` come from the local manifest.
* No authentication. Streamable HTTP binds to 127.0.0.1 with the SDK's DNS-rebinding protection. A tool call runs
  in a worker thread, and the local backend has no concurrency limit.
* `database_query` returns data only with PostgreSQL configured and loaded (phase 9, docs/database.md).
