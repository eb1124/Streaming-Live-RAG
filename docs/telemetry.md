# AdaptiveRAG phase 10A: OpenTelemetry tracing

Tracing around the existing service boundaries (`services/telemetry/`, the OpenTelemetry Python API and SDK). It is
observation only: no span changes what a service returns, and a tracing failure never reaches the application.
Nothing else changes. Retrieval, generation, the adaptive decisions, sessions, jobs, the MCP tool results and the
PostgreSQL queries are exactly as before (tested). Phase 10A is tracing only: no metrics, no log export.

Not implemented: a React frontend, durable job state.

## What is traced

| span | where | kind | attributes (besides the common ones) |
|---|---|---|---|
| `<METHOD> <route>`: `POST /query`, `POST /retrieve`, `POST /generate` | `TracingMiddleware` on the three FastAPI apps | server | `http.request.method`, `http.route`, `http.response.status_code` |
| `orchestrator.execute` | `OrchestratorWorker.handle`: one job | internal | `job.status`, `session.turn_index`, `session.continued`, `orchestrator.resolution`, `orchestrator.answer_status`, `orchestrator.abstention_reason`, `orchestrator.citation_count`; from the stored turn: `orchestrator.decision` (`retrieve`, `refine`, `wait`, `suppress`, `presentation`), `orchestrator.retrieval_required`, `session.anchor_turn_index` (the turn it was resolved against, refines or re-presents), `orchestrator.strategy`, `orchestrator.claim_count`, `orchestrator.answer_version` (1 for a question's first answer, +1 per refinement; a presentation has the version of the answer it lays out) |
| `retrieval.execute` | `RetrievalService.handle` | internal | `retrieval.mode`, `retrieval.k`, `retrieval.max_rounds`, `retrieval.evidence_count`, `retrieval.temporal.kind`, `retrieval.rounds`, `retrieval.stop_reason` |
| `retrieval.round` | each round of the phase 6 loop (`services/retrieval/rounds.py`) | internal | `retrieval.round.number`, `.strategy`, `.retrieved_count`, `.new_count`, `.excluded_count`, `.retained_count`, `.context_count`, `.promoted_count`, `.decision` (`refine` \| `stop`), `retrieval.coverage.decision`, `retrieval.coverage.achieved`, `retrieval.stop_reason` (last round) |
| `generation.execute` | `GenerationService.handle` | internal | `generation.provider`, `generation.model`, `generation.evidence_count`, `generation.status`, `generation.abstention_reason`, `generation.citation_count`, `generation.claim_count`, `generation.verification` (`accepted`, `rejected`, `not_run`), `generation.verification.problem_count`, `generation.llm_calls`, `generation.tokens.prompt` / `.completion` / `.total` (summed over the request's model calls, as the provider reports them; absent when it reports none) |
| `mcp.tool.execute` | every call of a known MCP tool (`services/mcp/server.py`) | internal | `mcp.tool.name`, `mcp.error.code` |
| `db.query` | `MetadataRepository.query`: one named query, with its connection | internal | `db.system.name` = `postgresql`, `db.operation.name` = `SELECT`, `db.query.name`, `db.namespace` (the schema), `db.response.returned_rows`, `db.query.truncated` |

Common to every span: `adaptiverag.service` (the component: `adaptiverag-api`, `-orchestrator`, `-retrieval`,
`-generation`, `-mcp`, `-persistence`), `adaptiverag.operation` (the span name), the correlation ids (below), and on
a failure `error.type`. A span's duration is its own start and end time.

Only boundaries are traced. Not traced: `GET /health`; the frozen pipeline's internals (hybrid search, reranking,
the prompt, the verifier); phases 1 to 3 inside the orchestrator; the Redis and RabbitMQ calls; an unknown MCP tool
name (a protocol error, not a tool call); the MCP transport itself; `python -m services.persistence migrate | load`.

## Span hierarchy

A query, with the services in one process (`services.inprocess.InProcess`, the tests) or the orchestrator, retrieval
and generation as separate processes:

```
POST /query                          api
└── orchestrator.execute             one job (see "Across RabbitMQ" for separate processes)
    ├── POST /retrieve               retrieval service, once per retrieval the controllers make
    │   └── retrieval.execute
    │       ├── retrieval.round      round 1          only with RETRIEVAL_MODE=iterative
    │       ├── retrieval.round      round 2
    │       └── ...
    └── POST /generate               generation service, once per answer
        └── generation.execute
```

An MCP tool call:

```
mcp.tool.execute                     document_search | metadata_lookup | database_query
├── POST /retrieve                   document_search with the remote backend (--retrieval remote)
│   └── retrieval.execute
│       └── retrieval.round ...      mode=iterative
├── retrieval.execute                document_search with the local backend
└── db.query                         database_query with PostgreSQL
```

**Propagation.** Inside a process a span is a child of the span that is current (OpenTelemetry's context; FastAPI
and the MCP server pass it into their worker threads). Across HTTP, `services.http.post` sends the current span's
W3C `traceparent` header and `TracingMiddleware` reads it, so the called service's spans join the caller's trace.
The api also accepts a client's `traceparent`. No header is sent when nothing is being recorded.

**Across RabbitMQ** the trace does not continue. The phase 4 `JobRequest` and the broker interface carry no trace
context, and phase 10A does not change those contracts. With the api and the orchestrator in separate processes,
`POST /query` and `orchestrator.execute` are therefore two traces, joined by `correlation_id` (the job id on both).
In one process the worker runs inside the request and `orchestrator.execute` is a child of `POST /query`.

**Rounds.** A `retrieval.round` span covers one round of the loop: the retrieval, the scoring and merging of its
chunks and the coverage decision. `TracedIterativeRetriever` is the phase 6 `IterativeRetriever` with two
observation points: the loop's start and end, and the loop's own request for the next refinement, which is where a
round ends. `adaptive/streaming` is not changed, and the traced loop returns the same evidence and the same
`RetrievalTrace` (tested for 1, 2 and 3 rounds and every stop reason the test corpus reaches).

## RetrievalTrace and spans

`RetrievalTrace` (phase 6, `adaptive/streaming/state.py`) stays the loop's execution state: deterministic, without
timings, returned with the retrieval and in `RetrievalResponse.trace`. It is not replaced, not changed and not
copied into a span. A round span takes counts and decisions from the round's entry (how many chunks, which
strategy, whether coverage was achieved) and adds what the trace deliberately lacks: when the round ran and how
long it took. The round's query, chunk ids, coverage reason and target organization stay in the trace only.

## Correlation ids and trace ids

They are different things and both are kept.

| | correlation ids (`services/correlation.py`, phase 7) | trace id / span id (OpenTelemetry) |
|---|---|---|
| made by | the application: `request_id` (api, MCP server), `job_id` (phase 4), `session_id` (the client) | the tracer, per trace and per span |
| travels in | the contracts (request and response bodies, job messages) | the `traceparent` HTTP header |
| checked | yes: a client refuses a response whose ids differ | no: nothing in the application reads it |
| exists without tracing | yes | no |

Every span carries the application's ids as attributes: `adaptiverag.request_id`, `adaptiverag.job_id`,
`adaptiverag.session_id` (each when known), and `correlation_id`: the job id when the work belongs to a job (the
id every downstream call of the job carries), otherwise the request id. Searching a trace backend for
`correlation_id = <job id>` finds every span of the job, in whatever trace. `Span.trace_id` / `Span.span_id` exist
for logs; no application logic uses them, and no contract changed.

## Where each required field is

| Field | Where |
|---|---|
| Execution timestamps | every span's start and end time; `retrieval.round` spans time each round |
| Request / correlation id | `correlation_id`, `adaptiverag.request_id` / `job_id` / `session_id` on every span |
| Retrieval trigger | `orchestrator.decision`, `orchestrator.retrieval_required`, `orchestrator.resolution`; each retrieval is a `retrieval.execute` span with `retrieval.mode`, `.rounds`, `.stop_reason` |
| Citations | `orchestrator.citation_count`, `generation.citation_count` on the spans. The cited chunk and document ids are not span attributes (see below): they are in the stored turn (`GET /sessions/{id}`), which the span names by `adaptiverag.session_id` + `session.turn_index`, and in the job result with the same `job_id` |
| Answer version lineage | `session.turn_index`, `session.anchor_turn_index`, `orchestrator.strategy`, `orchestrator.answer_version` |
| Verification outcome | `generation.verification`, `generation.verification.problem_count`, `generation.abstention_reason` |
| Token cost | `generation.tokens.prompt` / `.completion` / `.total`, `generation.llm_calls` per generation request; summed per job by `correlation_id`. Tokens only: no price is configured |
| MCP / database | `mcp.tool.execute`, `db.query` |

## What is never recorded

Span attributes go through one allow-list (`services/telemetry/spans.py`, `ATTRIBUTES`): a name that is not on it
is dropped, values are scalars, text is cut at 256 characters. No name on the list stands for a payload. Not
recorded, anywhere:

* the user's question, a retrieval query, a rewritten or refined query, a prompt;
* an answer, a claim, model output;
* chunk text, chunk ids, document titles, file names, organization names;
* HTTP headers (Authorization, cookies), the query string, raw URLs, request and response bodies;
* `DATABASE_URL`, its password, SQL text, query parameters, returned rows;
* exception messages and tracebacks.

**Errors.** An exception that leaves an instrumented boundary is recorded as an `exception` event with
`exception.type` (e.g. `services.http.ServiceError`), the attribute `error.type` and the status `ERROR`, and is
re-raised unchanged: the existing mapping (job `failed` and HTTP 502, MCP `isError` results and their codes,
`database_unavailable`) is untouched. The message and the traceback are left out on purpose: messages quote
payloads (a validation error echoes its input, a `ServiceError` the downstream response). They are in the service
logs, as before. Failures that are reported without an exception are read from what the application already
decided: an HTTP 5xx status (`error.type` = the status), a failed job's final event (`error.type` = the job
error's type), an MCP `isError` result (`mcp.error.code`). A 4xx response and an abstention are not errors.

## Configuration (`services/telemetry/config.py`)

The standard OpenTelemetry variables; each service process reads them at start (`telemetry.configure`).

| variable | default | |
|---|---|---|
| `OTEL_TRACES_EXPORTER` | `none`; `otlp` when an OTLP endpoint is set | `none` \| `console` \| `otlp` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` (or `..._TRACES_ENDPOINT`) | unset | the collector, e.g. `http://127.0.0.1:4318`; with the other `OTEL_EXPORTER_OTLP_*` variables it is read by the exporter itself |
| `OTEL_SERVICE_NAME` | the process's own name | the resource's `service.name`: `adaptiverag-api`, `-orchestrator`, `-retrieval`, `-generation`, `-mcp` |
| `OTEL_SDK_DISABLED` | `false` | `true`: no tracing, whatever the exporter |

**Without a collector (the default)** nothing is exported and no tracer provider is made: every span is a no-op, no
`traceparent` header is sent, and no connection is opened. The services run as in phase 9.

```
pip install -e ".[services,mcp,postgres,telemetry]"             # the SDK
$env:OTEL_TRACES_EXPORTER = "console"; python -m services.retrieval    # spans printed to stderr as they end

pip install -e ".[telemetry-otlp]"                               # OTLP over HTTP/protobuf
$env:OTEL_EXPORTER_OTLP_ENDPOINT = "http://127.0.0.1:4318"       # in every service's terminal
python -m services.retrieval
```

Use the same variables in every service process, so their spans reach the same backend; each process reports its
own `service.name`. A missing SDK or exporter package, or an invalid value, is logged as a warning at start and
leaves tracing off: tracing never stops a service from starting. An unreachable collector only makes the exporter
log its failed exports in the background.

The provider is kept in `services/telemetry/provider.py`, not registered as OpenTelemetry's global provider.
`configure` is idempotent (only the first call of a process configures). `python -m services.mcp.cli --stdio`
passes the `OTEL_*` variables on to the server it starts; the console exporter writes to stderr because stdout is
the stdio transport's channel.

## Files

`services/telemetry/`: `config.py` (settings), `provider.py` (`configure`, `tracer`, `shutdown`), `spans.py`
(`span`, `start`, `annotate`, `Span`, the attribute allow-list), `asgi.py` (`TracingMiddleware`, `trace_headers`),
`testing.py` (`capture`: the in-memory exporter). `services/retrieval/rounds.py`: the round spans.

Changed: the three `app.py` (the middleware), `retrieval/service.py`, `generation/service.py`,
`orchestrator/worker.py`, `orchestrator/client.py` (ids on the api's span), `http.py` (the trace headers),
`mcp/server.py`, `persistence/repository.py`, the five `__main__.py` (`configure`), `mcp/cli.py` (`OTEL_*` passed
on), `pyproject.toml`. Not changed: every contract, `adaptive/`, `generation/`, `retrieval/`, the migrations.

## Tests

`tests/test_telemetry.py`: 42 offline tests. Spans are read from the SDK's `InMemorySpanExporter`
(`services.telemetry.testing.capture`, which installs a fresh provider for one block and restores the previous
one, so tests do not see one another's spans). No collector, Docker, PostgreSQL, Redis or RabbitMQ.

* Foundation: settings from the environment; off by default; `configure` idempotent; a broken configuration
  leaves tracing off; OTLP configured from the environment without connecting; isolation of captures; the
  allow-list; nesting; an exception recorded and re-raised as the same object.
* HTTP: one server span per request with route template, status and correlation ids; 422 is not an error, 502 and
  an unhandled exception are; an unmatched path is named by its method only; `traceparent` in and out.
* The trace tree of a query through the in-process services, every parent taken from the actual span ids; the
  number of round spans equals the `RetrievalTrace`'s rounds (not a fixed number).
* Round spans equal to their trace entries; the traced loop equal to the phase 6 loop.
* Orchestrator, generation, MCP tool (every error code), remote MCP search, and named database query spans.
* Failures: the span of every boundary on the path is `ERROR`, the response is the phase 7 / 8 / 9 one.
* Tracing broken in three ways (the tracer raises, every span method raises, the propagator raises), in both
  retrieval modes: responses, model input, stored session and retrieval requests identical to tracing off.
* No payload: after a conversation and MCP calls, no question, query, answer, chunk text, chunk id, title, file
  name, organization, header or connection string occurs in any exported span, and every attribute is on the list.
* Layering: `opentelemetry` is imported only in `services/telemetry`; the frozen core does not know tracing.

## Limitations

* A trace does not cross RabbitMQ (see above): with separate api and orchestrator processes, join the two traces
  on `correlation_id`.
* The orchestrator span is one per job. Follow-up resolution, decomposition and the phase 1 decisions inside it
  are not spans, and the number of intents is not on the span (the job result does not carry it).
* Round boundaries come from the loop's refinement step (`IterativeRetriever._next`). If phase 6 changes how it
  asks for a refinement, `rounds.py` must follow; the tests compare the span count with the trace's rounds.
* `retrieval.round` spans exist only in iterative mode; the frozen single retrieval is one `retrieval.execute`.
* The retrieval and generation services' spans have no ids when a caller sends none (direct HTTP calls without
  `job_id` / `request_id`).
* Exception messages are not in spans; use the logs with the span's `correlation_id`.
* No sampling configuration beyond the SDK's own `OTEL_TRACES_SAMPLER`; no metrics; no log correlation.
* OTLP over gRPC is not supported (HTTP/protobuf only).
