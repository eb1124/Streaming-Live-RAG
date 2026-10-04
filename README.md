# StreamingLiveRAG — AdaptiveRAG

A grounded question-answering system over a corpus of institutional policy PDFs (travel, procurement, remote
work). Every answer is retrieved and cited against the source text, or the system explicitly abstains — it never
guesses. Built up in phases, from a single-shot retrieval pipeline to a service-oriented deployment with
asynchronous jobs, session persistence, MCP tool access, PostgreSQL-backed metadata, and OpenTelemetry tracing.

## What it does

- **Adaptive, bounded retrieval**: dense (the project's own arctic-m index) + BM25 fused by reciprocal rank,
  cross-encoder rerank, and an iterative loop that retrieves, judges coverage, and refines the query up to a
  round budget — no model in the loop.
- **Temporal version resolution**: when a policy has multiple dated versions, the system resolves which one the
  question is actually asking about (a specific date, or "currently") and drops the rest.
- **Multi-intent decomposition**: a question naming more than one organization or topic is split into intents,
  answered independently, and recombined.
- **Follow-up resolution**: a conversational turn is rewritten against earlier turns before retrieval runs.
- **Grounded generation with verification**: a relevance gate can abstain before any model call; the model sees
  labelled sources and must quote them; a verifier checks every claim's quotes against the source text before the
  answer is accepted.
- **Service architecture**: four services (api, orchestrator, retrieval, generation) connected by an asynchronous
  job queue and a session store, with an MCP tool server, PostgreSQL persistence, and OpenTelemetry tracing around
  every boundary. See [docs/services.md](docs/services.md) for the full request flow.
- **React frontend**: a workspace for asking questions and inspecting exactly how each answer was produced
  (retrieval rounds, citations, verification, telemetry).

Each build phase has its own doc in [docs/](docs/): [adaptive](docs/adaptive.md) (phase 1 controller),
[multi-intent](docs/multi_intent.md) (phase 2), [session](docs/session.md) (phase 3 follow-ups),
[jobs](docs/jobs.md) (phase 4 async queue), [session store](docs/session_store.md) (phase 5 Redis),
[streaming](docs/streaming.md) (phase 6 iterative retrieval), [services](docs/services.md) (phase 7),
[mcp](docs/mcp.md) (phase 8), [database](docs/database.md) (phase 9 PostgreSQL),
[telemetry](docs/telemetry.md) (phase 10A tracing).

## Running it

Requires Python 3.11+ and Node 18+. The corpus is already ingested, chunked, and indexed under `data/` (dense
index, BM25, chunk metadata) — no PDF processing is needed to run queries; see
[docs/ingestion.md](docs/ingestion.md) / [docs/chunking.md](docs/chunking.md) if you want to rebuild it from
`corpus/`.

```
python -m venv .venv
.venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU wheel; see pyproject.toml
.venv\Scripts\pip install -e ".[retrieval,generation,services]"

cp .env.example .env   # fill in GROQ_API_KEY (or run the generation service with GENERATION_PROVIDER=offline)

cd frontend && npm install
```

### Option A — no infrastructure (quickest)

The documented deployment (below) needs RabbitMQ and Redis. `scripts/run_local_api.py` runs the same real api and
orchestrator code with an in-memory broker and session store instead — only retrieval and generation are separate
processes, exactly as documented. Nothing about retrieval, generation, or the pipeline logic is stubbed.

```
python -m services.retrieval                                        # :8001
python -m services.generation                                       # :8002 (GENERATION_PROVIDER=offline for no live LLM calls)
python scripts/run_local_api.py                                     # :8000, in-memory broker/sessions
cd frontend && npm run dev                                          # http://localhost:5180
```

### Option B — full deployment (RabbitMQ + Redis, matches the design doc)

```
docker run -d --name adaptive-rabbitmq -p 5672:5672 rabbitmq:3
docker run -d --name adaptive-redis    -p 6379:6379 redis:7

$env:RABBITMQ_URL = "amqp://guest:guest@localhost:5672/%2F"
$env:REDIS_URL = "redis://localhost:6379/0"
python -m services.retrieval
python -m services.generation
python -m services.orchestrator
python -m services.api
cd frontend && npm run dev
```

Full detail, including the MCP server and PostgreSQL persistence: [docs/services.md](docs/services.md),
[docs/mcp.md](docs/mcp.md), [docs/database.md](docs/database.md).

## Tests

The full suite collects tests for the MCP, PostgreSQL, and telemetry layers too, so it needs those extras
installed even if you only ran the app with the smaller set above:

```
.venv\Scripts\pip install -e ".[mcp,postgres,telemetry]"
.venv\Scripts\python -m pytest -q                 # backend: unit + offline integration tests
cd frontend && npm run typecheck && npm run test  # frontend: types + unit tests
```

`pytest -q` also accepts live-infrastructure tests (`tests/test_jobs.py`, `tests/test_session_store.py`,
Postgres-marked tests) when `RABBITMQ_URL`, `REDIS_URL`, or `ADAPTIVERAG_TEST_DATABASE_URL` are set; they're
skipped otherwise.

## Project layout

```
adaptive/       phases 1-6: the controller, multi-intent, session, jobs, session store, iterative retrieval
chunking/       PDF → chunk pipeline
generation/     the retrieval stack loader and the grounded answerer (gate, prompt, model, verifier)
ingestion/      PDF extraction
retrieval/      dense/BM25/rerank implementation
services/       phase 7+: the four HTTP services, contracts, config, MCP tools, persistence, telemetry
scripts/        run_local_api.py — the no-infrastructure local runner
frontend/       the React + TypeScript workspace UI
evaluation/     offline evaluation runs and their results
corpus/         the source policy PDFs
data/           ingested chunks and the built retrieval index (generated, not hand-edited)
```
