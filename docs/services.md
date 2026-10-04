# AdaptiveRAG phase 7: service architecture

Four services around the existing pipeline. The phase 1 to 6 logic is the intelligence layer and is not rewritten:
the services only place it behind explicit, validated boundaries. RabbitMQ (phase 4) carries the asynchronous work,
Redis (phase 5) holds the sessions.

```
client
  │  POST /query {"session_id", "question"}                    ┌──────────────────────────────┐
  ▼                                                             │ Redis (phase 5 SessionStore) │
api (FastAPI, :8000)                                            └──────────────▲───────────────┘
  │  JobRequest (phase 4) ──► RabbitMQ "adaptiverag.jobs"                      │ get / put session
  │                                   │                                        │
  │                                   ▼                                        │
  │                        orchestrator (phase 4 Worker) ────────────────────────┘
  │                          SessionController (phase 3)
  │                          └ MultiIntentController (phase 2)
  │                            └ AdaptiveController (phase 1)
  │                              ├─ retriever= ──HTTP──► retrieval  (:8001)  frozen retrieve / phase 6 loop
  │                              └─ answerer=  ──HTTP──► generation (:8002)  frozen GroundedAnswerer + provider
  │                                   │
  │  JobEvent (phase 4) ◄── RabbitMQ "adaptiverag.job_events"   (processing, completed | failed)
  ▼
QueryResponse  (turn read from the session store)
```

Not implemented (later phases): a React frontend, durable job state. MCP: phase 8, docs/mcp.md. PostgreSQL (for the
MCP database_query tool): phase 9, docs/database.md. OpenTelemetry tracing: phase 10A, see Tracing below and
docs/telemetry.md. There is no ChromaDB in this
repository: dense retrieval is the project's own arctic-m index (retrieval/dense.py) with BM25 and RRF, unchanged.

## Services

| service | module | owns | does not |
|---|---|---|---|
| api | `services/api/` | the public HTTP interface; request validation; HTTP status mapping | retrieve, answer, resolve sessions |
| orchestrator | `services/orchestrator/` | consuming jobs; running phases 1-3 (and 6 through retrieval); loading/storing sessions | load an index or a model, call an LLM |
| retrieval | `services/retrieval/` | the loaded retrieval stack (dense, BM25, RRF, temporal resolution, cross-encoder); the phase 6 loop | answer |
| generation | `services/generation/` | the model provider and its credentials; the grounded answerer (gate, context, prompt, model, verifier) | retrieve |

Shared: `services/contracts.py` (contracts), `services/http.py` (service calls), `services/correlation.py`
(correlation ids), `services/config.py` (settings), `services/inprocess.py` (all four in one process, for the offline
evaluation and development without infrastructure), `services/telemetry/` (tracing, phase 10A).

Reused unchanged: the phase 4 job contracts, broker (`RabbitMQBroker`), client and worker (the orchestrator is a
`Worker` subclass that only binds correlation ids); the phase 5 `RedisSessionStore` and its codec; phases 1 to 6.
The orchestrator reaches the other services through two constructor hooks: `retriever=` (added in phase 6) and
`answerer=` (added in phase 7 to `AdaptiveController`, passed through `MultiIntentController` and
`SessionController`; default: the same `GroundedAnswerer` as before). Those three constructors are the only existing
files changed.

## Request flow

1. `POST /query` validates a `QueryRequest` and calls `OrchestratorClient.query`.
2. The client opens a broker connection, submits a phase 4 `JobRequest` (session_id, question; job_id is made here)
   and waits for the job's final `JobEvent`.
3. The orchestrator worker consumes the job, binds `job_id`/`session_id`, loads the session from the store, and runs
   `SessionController.ask`: follow-up resolution (phase 3), decomposition (phase 2), every retrieval through
   `RetrievalClient` → `POST /retrieve` (frozen retrieval, or the phase 6 loop with `RETRIEVAL_MODE=iterative`),
   every answer through `GenerationClient` → `POST /generate`. It stores the session and publishes the final event.
4. The client reads the answered turn from the session store (the job result names its index) and returns a
   `QueryResponse`: 200 when the job completed (answered or abstained), 502 when it failed, 504 on timeout.

## Contracts (`services/contracts.py`)

Pydantic, `extra="forbid"`, frozen, JSON via `model_dump_json`; `schema_version` 1. Payloads reuse the existing types
(`chunking.models.Chunk`, `generation.answer.GroundedAnswer`, `adaptive.streaming.state.RetrievalTrace`).

| contract | between | content |
|---|---|---|
| `QueryRequest` | client → api | `session_id` (`[A-Za-z0-9_.:-]`, ≤128), `question` (≤2000), optional `request_id` |
| `QueryResponse` | api → client | ids; job `status`, `error`; `turn_index`, `answer_status`, `text`, `citations` (Citation fields: document, pages, section, clause, effective date, chunk id); `resolution` (kind, reason, rewritten query, temporal constraint, organizations, anchor); `strategy` (phase 2); `answer_strategies` (phase 1); `evidence` (chunk ids shown to the model); `reused_citations` |
| `JobRequest` / `JobEvent` / `JobResult` | api ↔ orchestrator | phase 4, unchanged |
| `RetrievalRequest` | orchestrator → retrieval | ids; `query`; `mode` (`single` \| `iterative`); `max_rounds` (1-10); `k` |
| `RetrievalResponse` | retrieval → orchestrator | ids; `evidence` (`EvidenceItem`: the full `Chunk`, rank, scores, retriever); `resolution` (intent kind, dates, trigger, selected versions, dropped chunk ids, flags, candidate count); phase 6 `trace`; timings |
| `GenerationRequest` | orchestrator → generation | ids; `question`; `evidence`; `question_versions` |
| `GenerationResponse` | generation → orchestrator | ids; `answer` (`GroundedAnswer`: status, text, citations, claims, abstention, sources considered, model, raw output, verification) |

**Correlation**: `request_id` is the api's (the client may send one; returned in the response); `job_id` is the
phase 4 job's and is carried by every retrieval and generation request of that job, with `session_id`. Services echo
the ids; a client refuses a response whose ids differ. The retrieval response omits the full candidate lists
(`fused`, `pool`, `reranked`, the resolution's `kept`): they stay in the retrieval service; nothing above retrieval
reads them.

## Configuration (`services/config.py`)

| variable | used by | default |
|---|---|---|
| `API_HOST`, `API_PORT` | api | `127.0.0.1`, `8000` |
| `RETRIEVAL_HOST`, `RETRIEVAL_PORT` / `RETRIEVAL_URL` | retrieval / orchestrator | `127.0.0.1`, `8001` / `http://127.0.0.1:8001` |
| `GENERATION_HOST`, `GENERATION_PORT` / `GENERATION_URL` | generation / orchestrator | `127.0.0.1`, `8002` / `http://127.0.0.1:8002` |
| `RABBITMQ_URL` | api, orchestrator | required |
| `REDIS_URL` | api, orchestrator | required |
| `RETRIEVAL_MODE`, `MAX_ROUNDS` | orchestrator | `single`, `3` |
| `GENERATION_PROVIDER` | generation | `groq` (`GROQ_API_KEY`); `offline` = stub model, always abstains, no tokens |
| `QUERY_TIMEOUT_S`, `SERVICE_TIMEOUT_S` | api, orchestrator | `180`, `120` |
| `OTEL_TRACES_EXPORTER`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `OTEL_SDK_DISABLED` | every service | tracing off (`services/telemetry/config.py`) |

No credentials in code; a missing required variable stops the service at start with its name.

## Tracing (phase 10A)

OpenTelemetry tracing is implemented around the service boundaries (`services/telemetry/`; full description in
[telemetry.md](telemetry.md)). It only observes: responses, jobs, sessions and model input are identical with
tracing off, on, or broken (tested).

```
POST /query                          api          (server span: method, route template, status)
└── orchestrator.execute             one job: job status, turn index, resolution, answer status
    ├── POST /retrieve               retrieval service
    │   └── retrieval.execute        mode, k, evidence count, temporal kind, rounds, stop reason
    │       └── retrieval.round ...  one per round of the phase 6 loop (RETRIEVAL_MODE=iterative)
    └── POST /generate               generation service
        └── generation.execute       provider, model, evidence count, status, abstention reason, citations
```

* **HTTP.** Each app has `TracingMiddleware`: one server span per request, named by method and route template
  (never the raw URL). `GET /health` is not traced. `services.http.post` sends the W3C `traceparent` header of the
  current span, so the retrieval and generation spans join the orchestrator's trace.
* **RabbitMQ.** The job messages carry no trace context (the phase 4 contracts are unchanged). In one process
  (`InProcess`) `orchestrator.execute` is a child of `POST /query`; with separate processes they are two traces
  with the same `correlation_id`.
* **Correlation ids and trace ids.** The correlation ids above remain the application's mechanism: they travel in
  the contracts and are checked. A trace id is the tracer's and travels in a header; nothing reads it. Every span
  carries `adaptiverag.request_id` / `job_id` / `session_id` and `correlation_id` (the job id; without a job, the
  request id). No contract changed.
* **Never recorded:** the question, queries, the answer, prompts, chunk and document text, chunk ids, headers
  (Authorization, cookies), bodies, raw URLs, exception messages. Attributes go through one allow-list
  (`services/telemetry/spans.py`).
* **Errors.** An exception is recorded on the span (its type, status `ERROR`) and re-raised unchanged. A failed job
  marks `orchestrator.execute` from its final event, and a 5xx marks the HTTP span; the 200 / 502 / 504 mapping is
  unchanged.
* **Without a collector** (the default) nothing is exported, no tracer provider is made and every span is a no-op.
  `OTEL_TRACES_EXPORTER=console` prints spans to stderr; `OTEL_EXPORTER_OTLP_ENDPOINT=http://<collector>:4318`
  exports OTLP over HTTP (`pip install -e ".[telemetry,telemetry-otlp]"`). Set the variables in every service's
  terminal. A tracing misconfiguration is logged and leaves tracing off; it never stops a service.
* **Tests:** `tests/test_telemetry.py` (42 offline tests) reads spans from the SDK's in-memory exporter; no
  collector or other infrastructure.

## Local development

Infrastructure (Docker, no Compose needed):

```
docker run -d --name adaptive-rabbitmq -p 5672:5672 rabbitmq:3
docker run -d --name adaptive-redis    -p 6379:6379 redis:7
pip install -e ".[retrieval,generation,jobs,sessions,services]"
```

Four processes, each in its own terminal (PowerShell shown; the same variables in every terminal):

```
$env:RABBITMQ_URL = "amqp://guest:guest@localhost:5672/%2F"
$env:REDIS_URL = "redis://localhost:6379/0"

python -m services.retrieval                                   # loads the stack, then :8001
$env:GENERATION_PROVIDER = "offline"; python -m services.generation   # :8002 (omit for groq)
$env:RETRIEVAL_MODE = "iterative"; python -m services.orchestrator    # consumes adaptiverag.jobs
python -m services.api                                         # :8000
```

Without infrastructure: `services.inprocess.InProcess(stack, provider)` wires the same four services in one process
(TestClient, in-memory broker); the tests and `python -m evaluation.services.run` use it.

## Tests and offline evaluation

* `tests/test_services.py`: 23 offline tests: health endpoints; the query contract; invalid requests and service
  contracts rejected (422 / ValidationError, nothing queued); the api only delegates (200 / 502 / 504); a four-turn
  conversation through the services equals the direct pipeline (resolution, rewritten query, temporal constraint,
  strategies, evidence, answer, citations, model input, stored session), with frozen and with iterative retrieval;
  the orchestrator holds no index and makes no model call; the retrieval service returns the frozen retrieval and
  the phase 6 loop's evidence and trace; the generation service returns the frozen answer and abstention; queries
  travel as phase 4 jobs with the phase 4 lifecycle; a failing service fails the job (502, nothing stored); sessions
  continue across orchestrator restarts through the store; correlation ids reach every downstream request, nothing
  leaks between jobs, and a response with other ids is refused.
* `python -m evaluation.services.run` (`evaluation/services/results/offline.md`), real retrieval stack, stub model:
  69/69 turns equal to the direct pipeline in four suites: the 16 phase 2 cases, the 8 phase 3 conversations, the
  same conversations with a new deployment and a new Redis-store instance for every turn (phase 5), and the phase 3
  conversations plus the 2 phase 6 cases with the phase 6 loop; 42/42 conversations with identical model input and a
  stored session equal to the direct one.
* With the infrastructure running, `RABBITMQ_URL=... REDIS_URL=... pytest tests/test_jobs.py tests/test_session_store.py`
  also runs the phase 4 RabbitMQ and phase 5 Redis tests (2026-10-01: 50 passed, 0 skipped with test_services.py).

## Manual smoke test (2026-10-01)

Real RabbitMQ (`rabbitmq:3`) and Redis (`redis:7`) in Docker, the four services as separate processes
(`GENERATION_PROVIDER=offline`, `RETRIEVAL_MODE=iterative`; the api on `API_PORT=8010` because port 8000 was taken by
another program on this machine). No live LLM call: the offline provider answers "insufficient evidence", so the
answers below are abstentions; retrieval, sessions, the queue and the gate/verifier path are the real ones.

```
curl http://127.0.0.1:8001/health  -> {"status":"ok","service":"retrieval","chunks":466,"reranker":true}
curl http://127.0.0.1:8002/health  -> {"status":"ok","service":"generation","provider":"offline-stub","model":"none"}
curl http://127.0.0.1:8010/health  -> {"status":"ok","service":"api"}

POST /query {"session_id":"smoke-p7-014523","request_id":"smoke-req-1",
             "question":"What did UConn's February 2026 procedures say about when a University Travel Card is suspended?"}
  -> 200 completed, job 0215ede7…, turn 0, self_contained, temporal "February 2026", strategy delegate/single,
     6 evidence chunks, first travel-and-entertainment-procedures-final-ccccf9::005-bb1bf7113880 (February version),
     abstained (model_insufficient_evidence)
POST /query {"session_id":"smoke-p7-014523","request_id":"smoke-req-2",
             "question":"Does that still apply under the current procedures?"}
  -> 200 completed, job 059ef400…, turn 1, follow_up, temporal "current", query "(Earlier in this conversation: What
     did UConn's procedures say about when a University Travel Card is suspended). Does that still apply under the
     current procedures?", evidence from the July 2026 version (…ca903b::005-73722dbc7d99)
```

Logs: the retrieval service logged `retrieve job=0215ede7… session=smoke-p7-014523 mode=iterative evidence=10
rounds=1` and the same for job 059ef400…; the generation service logged `generate job=… session=smoke-p7-014523
evidence=10 status=abstained`; the api logged two `POST /query 200`.

Redis: `redis-cli EXISTS adaptiverag:session:smoke-p7-014523` → 1 (12,229 bytes; format adaptiverag.session v1;
turn 0 self_contained, turn 1 follow_up anchored to turn 0).

RabbitMQ, asynchronous path: with the orchestrator stopped, a third query ("What happens after 90 days?") waited in
the queue (`rabbitmqctl list_queues`: `adaptiverag.jobs 1 message, 0 consumers`) while the api waited for its event.
A new orchestrator process consumed it, loaded the session from Redis and answered it as turn 2, a follow-up
anchored to turn 1 with the inherited constraint "current" (job 5617c715…); afterwards `adaptiverag.jobs 0 messages,
1 consumer` and three turns in Redis.

Not shown by this smoke test: an answer with citations. That needs the model (`GENERATION_PROVIDER=groq`), and no
live call was made in this phase; the offline tests and evaluation check that answers and citations pass through the
services unchanged.

## Limitations

* One query at a time per api process, and one api per events queue (the phase 4 client consumes every event on
  it); one orchestrator per jobs queue (phase 4/5: sessions are loaded, answered and stored without locking).
* The api waits synchronously for the job (up to `QUERY_TIMEOUT_S`); there is no job-status endpoint yet.
* Service calls are plain HTTP without authentication, retries or TLS; a failed call fails the job.
* The orchestrator reads the chunk catalog (`data/chunks`) for the corpus organizations; the files must be the ones
  the retrieval service indexed.
* The retrieval response carries the full chunk of every evidence item (a few KB each), not references.
* A trace does not cross RabbitMQ; the api's and the orchestrator's spans are joined by `correlation_id` when they
  run as separate processes (phase 10A, docs/telemetry.md).
