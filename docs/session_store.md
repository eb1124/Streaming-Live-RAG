# AdaptiveRAG phase 5: persistent session state

The phase 4 worker kept each conversation's phase 3 `Session` in its own memory, so a worker restart forgot every
conversation: the next "Does that apply currently?" in a session found nothing to refer to. Phase 5 puts sessions
behind a small store; with Redis they survive restarts. Phase 3 resolution, the job contracts and lifecycle, and
everything below the session controller are unchanged.

```
job (session_id, question) -> Worker
    session = store.get(session_id) or Session()
    turn = SessionController.ask(session, question)          phase 3, unchanged
    store.put(session_id, session)
                 |
        SessionStore: InMemorySessionStore (default) | RedisSessionStore (REDIS_URL)
```

## Files

| file | role |
|---|---|
| `adaptive/session_store/store.py` | `SessionStore` protocol, `InMemorySessionStore` |
| `adaptive/session_store/codec.py` | `encode_session` / `decode_session`: the session as versioned JSON |
| `adaptive/session_store/redis_store.py` | `RedisSessionStore`: the only module that imports the Redis client |
| `adaptive/jobs/worker.py` | changed: `Worker(..., sessions=store)` loads and stores the session around `ask()` |
| `adaptive/jobs/__main__.py` | changed: `worker` keeps sessions in Redis when `REDIS_URL` is set |
| `evaluation/session_store/run.py` | the phase 3 conversations through jobs on a store vs phase 3 |
| `tests/test_session_store.py` | 12 offline tests, plus 2 that run only with `REDIS_URL` |

## Interface

```python
class SessionStore(Protocol):
    def get(self, session_id: str) -> Session | None: ...     # None: no such session yet
    def put(self, session_id: str, session: Session) -> None: ...
    def delete(self, session_id: str) -> None: ...            # no error if absent
```

* **InMemorySessionStore**: a `dict` of `Session` objects, kept by reference in the process: exactly phase 4's
  behaviour, and the worker's default. The tests, the offline evaluations and the demo use it.
* **RedisSessionStore**: one Redis string per session, key `adaptiverag:session:<session_id>`, value the encoded
  session. `get` decodes a fresh `Session` on every call; `put` writes the whole session. No expiry: a session stays
  until `delete`. Built from `REDIS_URL` (`RedisSessionStore.from_env()`) or an explicit URL; a missing URL raises
  `SessionStoreConfigError`. It accepts any client with `get` / `set` / `delete`, which is how the offline tests and
  evaluation run it without a server.

## Serialization

```json
{"format": "adaptiverag.session", "version": 1, "session": {"turns": [ ... ]}}
```

The session is the complete phase 3 state as the existing dataclasses define it: every `Turn` (question,
`Resolution`, `reused_citations`) with its `MultiIntentAnswer`, the phase 1 `AdaptiveAnswer`s, `Decision`s and
`Citation`s. A pydantic `TypeAdapter(Session)` dumps it to JSON and validates it back into the same classes, so
`Session`, `Turn` and `Resolution` are not redesigned and a decoded session is field-for-field equal to the original.
Phase 3 reads from the previous turn its resolution (kind, topic, temporal constraint, organizations), its question
and index, how many intents it asked, and its cited chunks: all preserved, so a follow-up resolves identically.

JSON only: nothing is unpickled and no code runs on load. A body with another format or version, or one that does
not validate, raises `SessionFormatError`; the job then fails. An encoded session is about 5 to 18 KB for the
evaluation conversations (every turn keeps its full answer record).

## Configuration

`pip install -e ".[sessions]"` (redis-py) and `REDIS_URL`, e.g. `redis://localhost:6379/0` (credentials, if any, go
in the URL, never in code). `python -m adaptive.jobs worker` uses Redis when `REDIS_URL` is set and says so; without
it, sessions stay in the worker's memory as in phase 4. Nothing else needs Redis: without `REDIS_URL` the two Redis
tests are skipped.

## Worker integration

For each job the worker loads the session (a new `Session` if there is none), runs the unchanged
`SessionController.ask`, then stores the session. Loading, answering and storing are one step for the job's outcome:
an exception in any of them (the pipeline, Redis unreachable, a session that does not decode) fails the job, and a
job that completed always has its turn stored. A failed job stores nothing (`ask` appends the turn only when the
pipeline returns). The lifecycle, the job contracts, abstentions as completed jobs and duplicate delivery are as in
phase 4.

## Evaluation

`python -m evaluation.session_store.run` (`evaluation/session_store/results/offline.md`): the 8 phase 3 conversations
(17 turns) run directly, through jobs on an `InMemorySessionStore`, and through jobs where every turn gets a new worker
and a new `RedisSessionStore` over an in-process client, so each turn starts from the encoded session alone. 17/17
turns equal the committed phase 3 evaluation (resolution, rewritten query, temporal constraint, organizations,
strategy, versions, context) and the direct run (answer, citations); model input is identical in 8/8 conversations,
and 8/8 stored sessions equal the direct session field for field. No live LLM calls.

## Limitations

* **Duplicate delivery after a restart.** The worker's record of finished jobs is still in its memory. A job
  redelivered to a *restarted* worker (it crashed after storing the turn, before acknowledging the message) runs
  again and appends its turn a second time, now that the session outlives the worker. Phase 4 did not have this
  case because the session was lost too. Fixing it needs the finished-job record in the store as well.
* One worker per jobs queue: `get`, `ask`, `put` is not atomic, so two workers on one session would overwrite each
  other's turns (no locking in this phase).
* A session only grows; there is no expiry, size limit or pruning of old turns.
* The format has one version; reading an older format after a change of the dataclasses needs a migration.
