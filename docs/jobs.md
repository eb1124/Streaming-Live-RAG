# AdaptiveRAG phase 4: asynchronous job workflow

An event-driven boundary around the existing pipeline: a question is submitted as a job on a queue, a worker runs the
phase 3 session controller on it, and the job's state and result come back as events. Nothing below the worker
changes: retrieval, temporal resolution, the prompt, the verifier and phases 1 to 3 run exactly as before.

```
JobClient.submit(question, session_id) -> job_id           state: queued (recorded by the client)
   |  JobRequest (JSON)
   v
"adaptiverag.jobs" queue           Broker: InMemoryBroker (tests, demo) | RabbitMQBroker (pika)
   |
   v
Worker.handle -> SessionController.ask(session, question)   phase 3, unchanged; one Session per session_id
   |  JobEvent (JSON): processing, then completed (JobResult) | failed (error)
   v
"adaptiverag.job_events" queue
   |
   v
JobClient.poll / wait -> JobTracker: status, result, error per job_id
```

## Files

| file | role |
|---|---|
| `adaptive/jobs/contracts.py` | `JobRequest`, `JobEvent`, `JobResult`, `JobStatus`, the lifecycle (`TRANSITIONS`), queue names |
| `adaptive/jobs/broker.py` | `Broker` protocol (`publish`, `consume`) and `InMemoryBroker` |
| `adaptive/jobs/rabbitmq.py` | `RabbitMQBroker`: the same protocol over RabbitMQ; the only module that imports pika |
| `adaptive/jobs/worker.py` | `Worker`: consumes requests, runs the session controller, publishes events |
| `adaptive/jobs/client.py` | `JobClient` (submit, poll, wait, status, result, error) and `JobTracker` (the lifecycle) |
| `adaptive/jobs/__main__.py` | `demo` (in memory), `worker` and `submit` (RabbitMQ) |
| `evaluation/jobs/run.py` | the phase 3 conversations through the workflow vs the controller called directly |
| `tests/test_jobs.py` | 12 offline tests, plus 1 RabbitMQ test that runs only with `RABBITMQ_URL` |

## Contract

Pydantic models, validated on both sides; unknown fields are rejected; `schema_version` is 1.

* `JobRequest`: `job_id` (uuid4 hex, made by the client), `session_id`, `question` (verbatim), `submitted_at`.
* `JobEvent`: `job_id`, `session_id`, `status`, `at`, and exactly one payload where the status requires it:
  `result` on `completed`, `error` (`{"type", "message"}`) on `failed`, none otherwise.
* `JobResult`: a summary of the phase 3 turn: turn index, resolution kind and reason, answer status, answer text,
  citations (the `Citation` fields), abstention reason. Retrieved evidence, the phase 1/2 records and the model
  output stay in the worker's session: the event layer carries what a caller needs to show the answer, nothing more.

Lifecycle: `queued -> processing -> completed | failed`, or `queued -> failed` when the worker rejects the request
before running it. `JobTracker` raises `InvalidTransition` on anything else and ignores an event identical to the
job's last one.

The **job** status says whether the pipeline ran; the **answer** status says what it found. An abstention (no
evidence, an unresolved reference, or a provider error, which the answerer already turns into an abstention) is a
`completed` job with `answer_status: "abstained"`. A job fails only when an exception escapes the controller.

## Failure handling

* The worker catches an exception from `SessionController.ask`, logs it with its traceback and publishes `failed`
  with its type and message, then goes on with the next job. `ask` appends the turn only after the pipeline returns,
  so a failed job leaves its session unchanged.
* An invalid request with a `job_id` gets a `failed` event (`ValidationError`). A message without one cannot be
  reported: the handler raises and the broker logs it and rejects it without requeue.
* No retries and no dead-letter queue: a rejected message is dropped (logged). A job is never retried automatically.
* Duplicate delivery: the worker remembers the final event of every job it finished; a request delivered again is
  not run again (a turn is never appended twice), its final event is republished and the tracker ignores the repeat.

## Why the broker is an adapter

`Broker` has two methods and deals only in bytes. The contracts, the worker and the client depend on it, never on
pika, so the RAG side cannot pick up transport details, and the transport can be swapped (another broker, or HTTP in
front of the client) without touching them. RabbitMQ semantics that matter (acknowledge after the handler returns,
reject without requeue on an exception) are defined on the protocol and implemented by both brokers.

`InMemoryBroker` keeps FIFO queues in the process and delivers synchronously; it records everything published and
every rejected message. The tests, the offline evaluation and the demo run the whole flow on it: no server, no
network. `RabbitMQBroker` uses durable queues, persistent messages and prefetch 1 on the default exchange.

## Running it

```
python -m adaptive.jobs demo --offline 'What did UConn''s February 2026 procedures say about ...?' 'Does that still apply under the current procedures?'
python -m adaptive.jobs worker            # RabbitMQ at RABBITMQ_URL (default amqp://guest:guest@localhost:5672/%2F)
python -m adaptive.jobs submit --session s1 'first question' 'follow-up'
```

`demo` runs client, broker and worker in one process; `--offline` answers with a stub model (no tokens). The RabbitMQ
path needs `pip install -e ".[jobs]"` (pika) and a server; `RABBITMQ_URL=... pytest tests/test_jobs.py` then also
runs the RabbitMQ round-trip test.

## Evaluation

`python -m evaluation.jobs.run` (`evaluation/jobs/results/offline.md`): the 8 phase 3 conversations (17 turns) as jobs
through the in-memory broker, real retrieval stack, stub model. 17/17 turns go `queued -> processing -> completed` and
have the same resolution, query, answer and citations as the controller called directly; the model input is
byte-identical in 8/8 conversations.

## Limitations (phase 4 scope)

* One worker per jobs queue, and one client per events queue. Sessions live in the worker's memory: turns of a
  session run in queue order only because a single worker takes one job at a time; a worker restart loses the
  sessions (and the duplicate-delivery record).
* The events queue is shared: a second client would consume the first one's events.
* No retries, dead-lettering, persistence of job state, or timeouts for jobs a worker never picks up.

## Later

The same contracts let the pieces run as separate services: an orchestration service that owns sessions (in shared
storage, so several workers can serve one queue with per-session ordering), retrieval and ingestion workers behind
their own queues, and an API in front of `JobClient` that returns the `job_id` at once and serves status and results
from stored events. Each of those replaces an in-memory part here without changing the contracts or the worker's
call into the pipeline.
