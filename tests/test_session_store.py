"""Persistent session state (phase 5, offline): the session stores, the session codec, and the phase 4 worker on a
store, with phase 3 behaviour unchanged.

RedisSessionStore runs here over an in-process stand-in for the Redis client (a dict of bytes), so its encode/decode
path is exercised without a server. The tests marked for Redis run only when REDIS_URL is set.
"""

import os
import subprocess
import sys
import uuid
from dataclasses import asdict

import pytest

from adaptive.jobs.broker import InMemoryBroker
from adaptive.jobs.client import JobClient
from adaptive.jobs.contracts import JobStatus
from adaptive.jobs.worker import Worker
from adaptive.multi.controller import MultiIntentAnswer
from adaptive.session.controller import SessionController
from adaptive.session.state import FOLLOW_UP, SELF_CONTAINED, UNRESOLVED, Session
from adaptive.session_store.codec import FORMAT, VERSION, SessionFormatError, decode_session, encode_session
from adaptive.session_store.redis_store import RedisSessionStore, SessionStoreConfigError
from adaptive.session_store.store import InMemorySessionStore
from generation.answer import Citation
from generation.providers import StubProvider
from tests.test_generation_verification import SERIES
from tests.test_multi_intent_controller import stack
from tests.test_session import ALIASES, CHUNKS, JUL_CARD, LODGE, Q_FEB, Q_LODGE, Q_TWO, top_source

C, F = JobStatus.COMPLETED, JobStatus.FAILED
FOLLOW = "Does that apply currently?"


class FakeRedis:
    """The part of redis.Redis the store uses: bytes values in a dict shared by every store over it."""

    def __init__(self):
        self.data: dict[str, bytes] = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        assert isinstance(value, bytes)
        self.data[key] = value

    def delete(self, key):
        self.data.pop(key, None)


def controller():
    provider = StubProvider(top_source)
    return SessionController(stack(*CHUNKS), provider, ALIASES), provider


def rich_session() -> Session:
    """Every kind of phase 3 turn: self-contained, a temporal follow-up, a two-intent turn, an unresolved turn."""
    ctl, _ = controller()
    s = Session()
    for q in (Q_FEB, FOLLOW, Q_TWO, FOLLOW):
        ctl.ask(s, q)
    return s


def run_jobs(worker, client, questions, session_id="s1"):
    ids = []
    for q in questions:
        ids.append(client.submit(q, session_id))
        worker.run()
        client.poll()
    return ids


# ---------------------------------------------------------------- stores


def test_in_memory_store_round_trip():
    store, s = InMemorySessionStore(), Session()
    assert store.get("s1") is None
    store.put("s1", s)
    assert store.get("s1") is s and store["s1"] is s and list(store) == ["s1"]
    store.delete("s1")
    store.delete("never-stored")  # no error
    assert store.get("s1") is None and store == {}


def test_redis_store_needs_configuration(monkeypatch):
    with pytest.raises(SessionStoreConfigError):
        RedisSessionStore()
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(SessionStoreConfigError, match="REDIS_URL"):
        RedisSessionStore.from_env()


def test_redis_store_round_trip_over_a_client():
    backend = FakeRedis()
    s = rich_session()
    RedisSessionStore(client=backend).put("s1", s)
    assert list(backend.data) == ["adaptiverag:session:s1"]
    loaded = RedisSessionStore(client=backend).get("s1")  # a fresh store instance, same backend
    assert loaded is not s and asdict(loaded) == asdict(s)
    assert RedisSessionStore(client=backend).get("other") is None
    RedisSessionStore(client=backend).delete("s1")
    assert backend.data == {}


def test_only_the_redis_store_imports_the_redis_client():
    code = ("import sys, adaptive.jobs.worker, adaptive.jobs.__main__, adaptive.session_store.store, "
            "adaptive.session_store.codec; print('redis' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


# ---------------------------------------------------------------- codec


def test_codec_preserves_the_complete_phase3_state():
    s = rich_session()
    assert [t.resolution.kind for t in s.turns] == [SELF_CONTAINED, FOLLOW_UP, SELF_CONTAINED, UNRESOLVED]
    body = encode_session(s)
    assert body.startswith(b'{"format":"%s","version":%d,' % (FORMAT.encode(), VERSION))
    r = decode_session(body)
    assert asdict(r) == asdict(s)  # every field of every turn, answer, phase 1 record and citation
    assert decode_session(encode_session(r)) == r and encode_session(r) == body  # stable
    t0, t1 = r.turns[0], r.turns[1]
    assert isinstance(t0.answer, MultiIntentAnswer) and isinstance(t0.citations[0], Citation)
    assert t1.answer.phase1[0].decision.strategy == "single" and t1.answer.intents[0]["selected_versions"] == {
        SERIES: ["proc-jul"]}
    # what phase 3 reads from a previous turn when it resolves a follow-up
    for a, b in zip(r.turns, s.turns):
        assert (a.index, a.question, a.resolution, a.intents, a.status, a.text, a.cited_chunks, a.reused_citations) == (
            b.index, b.question, b.resolution, b.intents, b.status, b.text, b.cited_chunks, b.reused_citations)
    assert r.turns[3].answer is None and r.turns[2].intents == 2


def test_codec_refuses_what_it_did_not_write():
    good = encode_session(Session())
    for body, match in [(b"not json", "not JSON"), (b'{"format": "other"}', "not an adaptiverag session"),
                        (good.replace(b'"version":1', b'"version":2'), "version 2"),
                        (b'{"format": "adaptiverag.session", "version": 1, "session": {"turns": [{"index": "x"}]}}',
                         "invalid session")]:
        with pytest.raises(SessionFormatError, match=match):
            decode_session(body)


def test_a_loaded_session_behaves_like_the_in_memory_one():
    live_ctl, live_provider = controller()
    live = Session()
    live_ctl.ask(live, Q_FEB)
    loaded = decode_session(encode_session(live))
    loaded_ctl, loaded_provider = controller()
    loaded_provider.calls.extend(live_provider.calls)  # same history, so the call lists compare turn by turn
    a = live_ctl.ask(live, FOLLOW)
    b = loaded_ctl.ask(loaded, FOLLOW)
    assert loaded_provider.calls == live_provider.calls  # byte-identical model input for the follow-up
    assert (b.resolution, b.text, b.cited_chunks, b.reused_citations) == (a.resolution, a.text, a.cited_chunks,
                                                                         a.reused_citations)
    assert b.cited_chunks == [JUL_CARD.chunk_id] and asdict(loaded) == asdict(live)


# ---------------------------------------------------------------- worker on a store


def test_worker_on_a_store_keeps_phase3_behaviour():
    direct_ctl, direct_provider = controller()
    session = Session()
    expected = [direct_ctl.ask(session, q) for q in (Q_FEB, FOLLOW)]
    ctl, provider = controller()
    broker = InMemoryBroker()
    worker = Worker(ctl, broker, sessions=RedisSessionStore(client=FakeRedis()))
    client = JobClient(broker)
    jobs = run_jobs(worker, client, [Q_FEB, FOLLOW])
    assert provider.calls == direct_provider.calls
    for job_id, turn in zip(jobs, expected):
        r = client.result(job_id)
        assert client.tracker.history[job_id] == [JobStatus.QUEUED, JobStatus.PROCESSING, C]
        assert (r.resolution, r.text, [c["chunk_id"] for c in r.citations]) == (turn.resolution.kind, turn.text,
                                                                                turn.cited_chunks)
    assert asdict(worker.sessions.get("s1")) == asdict(session)


def test_jobs_of_one_session_share_its_state_and_sessions_stay_apart():
    backend = FakeRedis()
    ctl, _ = controller()
    broker = InMemoryBroker()
    worker, client = Worker(ctl, broker, sessions=RedisSessionStore(client=backend)), JobClient(broker)
    a1 = run_jobs(worker, client, [Q_LODGE], "a")[0]
    b1 = run_jobs(worker, client, ["Does this policy allow a personal credit card?"], "b")[0]
    a2 = run_jobs(worker, client, ["Does this policy allow a personal credit card?"], "a")[0]
    assert client.result(a1).turn_index == 0
    assert client.result(b1).resolution == SELF_CONTAINED and client.result(b1).turn_index == 0  # nothing from "a"
    assert client.result(a2).resolution == FOLLOW_UP and client.result(a2).turn_index == 1  # sees a's first turn
    assert [c["chunk_id"] for c in client.result(a2).citations] == [LODGE.chunk_id]
    store = RedisSessionStore(client=backend)
    assert [len(store.get(s).turns) for s in ("a", "b")] == [2, 1]
    assert sorted(backend.data) == ["adaptiverag:session:a", "adaptiverag:session:b"]


def test_a_new_worker_continues_a_session_stored_by_a_previous_one():
    backend = FakeRedis()
    broker = InMemoryBroker()
    client = JobClient(broker)
    first = Worker(controller()[0], broker, sessions=RedisSessionStore(client=backend))
    run_jobs(first, client, [Q_FEB])
    del first  # the worker, its store and every Session object are gone; only the stored bytes remain
    ctl, provider = controller()
    second = Worker(ctl, broker, sessions=RedisSessionStore(client=backend))
    job = run_jobs(second, client, [FOLLOW])[0]
    r = client.result(job)
    assert r.resolution == FOLLOW_UP and r.turn_index == 1
    assert [c["chunk_id"] for c in r.citations] == [JUL_CARD.chunk_id]  # "that" resolved to turn 1, now current
    assert provider.calls[0][1].startswith("Question: (Earlier in this conversation: What did TU's procedures say")


def test_a_store_error_fails_the_job_and_stores_nothing():
    class Down(FakeRedis):
        def set(self, key, value):
            raise ConnectionError("redis unavailable")

    backend = Down()
    broker = InMemoryBroker()
    worker, client = Worker(controller()[0], broker, sessions=RedisSessionStore(client=backend)), JobClient(broker)
    job = run_jobs(worker, client, [Q_LODGE])[0]
    assert client.status(job) == F and client.error(job) == {"type": "ConnectionError", "message": "redis unavailable"}
    assert backend.data == {}


def test_in_memory_default_keeps_phase4_behaviour():
    worker = Worker(controller()[0], InMemoryBroker())
    assert isinstance(worker.sessions, InMemorySessionStore) and worker.sessions == {}


# ---------------------------------------------------------------- Redis (only with a server)

needs_redis = pytest.mark.skipif(not os.environ.get("REDIS_URL"), reason="Redis test disabled (set REDIS_URL)")


@needs_redis
def test_redis_round_trip_with_a_fresh_store():
    sid = f"test-{uuid.uuid4().hex}"
    s = rich_session()
    RedisSessionStore.from_env().put(sid, s)
    fresh = RedisSessionStore.from_env()
    try:
        loaded = fresh.get(sid)
        assert loaded is not None and asdict(loaded) == asdict(s)
    finally:
        fresh.delete(sid)
    assert RedisSessionStore.from_env().get(sid) is None


@needs_redis
def test_redis_worker_continues_after_the_session_object_is_discarded():
    sid = f"test-{uuid.uuid4().hex}"
    broker = InMemoryBroker()
    client = JobClient(broker)
    try:
        run_jobs(Worker(controller()[0], broker, sessions=RedisSessionStore.from_env()), client, [Q_FEB], sid)
        job = run_jobs(Worker(controller()[0], broker, sessions=RedisSessionStore.from_env()), client, [FOLLOW], sid)[0]
        r = client.result(job)
        assert (r.resolution, r.turn_index) == (FOLLOW_UP, 1)
        assert [c["chunk_id"] for c in r.citations] == [JUL_CARD.chunk_id]
    finally:
        RedisSessionStore.from_env().delete(sid)
