"""Asynchronous job workflow (phase 4, offline): contracts, submission, consumption, lifecycle, results, failures,
duplicate delivery, and the phase 3 session controller unchanged behind the queue.

Everything runs over the in-memory broker and the keyword-overlap stand-ins with the stub model of
tests/test_session.py. The RabbitMQ test runs only when RABBITMQ_URL is set.
"""

import json
import logging
import os
import uuid

import pytest
from pydantic import ValidationError

from adaptive.jobs.broker import InMemoryBroker
from adaptive.jobs.client import InvalidTransition, JobClient, JobTracker
from adaptive.jobs.contracts import EVENTS_QUEUE, JOBS_QUEUE, JobEvent, JobRequest, JobResult, JobStatus
from adaptive.jobs.worker import Worker
from adaptive.session.controller import SessionController
from adaptive.session.state import Session
from generation.providers import StubProvider
from tests.test_generation_verification import SERIES
from tests.test_multi_intent_controller import stack
from tests.test_session import ALIASES, CHUNKS, JUL_CARD, LODGE, Q_FEB, Q_LODGE, Q_TWO, top_source

Q, P, C, F = JobStatus.QUEUED, JobStatus.PROCESSING, JobStatus.COMPLETED, JobStatus.FAILED


def controller():
    provider = StubProvider(top_source)
    return SessionController(stack(*CHUNKS), provider, ALIASES), provider


def rig(ctl=None):
    provider = None
    if ctl is None:
        ctl, provider = controller()
    broker = InMemoryBroker()
    return Worker(ctl, broker), JobClient(broker), broker, provider


def run_job(worker, client, question, session_id="s1"):
    job_id = client.submit(question, session_id)
    worker.run()
    client.poll()
    return job_id


class Flaky:
    """A session controller whose pipeline raises for questions containing "boom"."""

    def __init__(self, inner):
        self.inner = inner

    def ask(self, session, question):
        if "boom" in question:
            raise RuntimeError("retrieval index unavailable")
        return self.inner.ask(session, question)


# ---------------------------------------------------------------- contracts


def test_contracts_round_trip_and_reject_invalid_messages():
    req = JobRequest.new("s1", "What is TU's rule?")
    assert JobRequest.decode(req.encode()) == req and len(req.job_id) == 32
    result = JobResult(turn_index=0, resolution="self_contained", resolution_reason="first turn",
                       answer_status="answered", text="t", citations=[{"number": 1, "chunk_id": "c"}])
    done = JobEvent.of(req, C, result=result)
    assert JobEvent.decode(done.encode()) == done
    with pytest.raises(ValidationError):
        JobRequest.decode(b'{"job_id": "x", "session_id": "s"}')  # no question
    with pytest.raises(ValidationError):
        JobRequest.decode(req.encode().replace(b'"question"', b'"extra": 1, "question"'))  # unknown field
    with pytest.raises(ValidationError):
        JobEvent.decode(done.encode().replace(b'"completed"', b'"done"'))  # unknown status
    with pytest.raises(ValidationError):
        JobEvent.of(req, C)  # completed without a result
    with pytest.raises(ValidationError):
        JobEvent.of(req, P, error={"type": "X", "message": "m"})  # an error on a non-failed event


# ---------------------------------------------------------------- submission, consumption, lifecycle, results


def test_submit_records_queued_and_publishes_one_request():
    _, client, broker, _ = rig()
    job_id = client.submit(Q_LODGE, "s1")
    assert client.status(job_id) == Q and client.result(job_id) is None
    assert broker.pending(JOBS_QUEUE) == 1
    req = JobRequest.decode(broker.queues[JOBS_QUEUE][0])
    assert (req.job_id, req.session_id, req.question) == (job_id, "s1", Q_LODGE)


def test_worker_consumes_the_queue():
    worker, client, broker, _ = rig()
    client.submit(Q_LODGE, "s1")
    client.submit("Does this policy allow a personal credit card?", "s1")
    assert worker.run() == 2 and broker.pending(JOBS_QUEUE) == 0
    assert len(worker.sessions["s1"].turns) == 2 and broker.pending(EVENTS_QUEUE) == 4  # processing + final, each


def test_successful_job_goes_queued_processing_completed_with_its_result():
    worker, client, _, provider = rig()
    job_id = run_job(worker, client, Q_LODGE)
    assert client.tracker.history[job_id] == [Q, P, C] and client.error(job_id) is None
    r = client.result(job_id)
    assert (r.turn_index, r.resolution, r.answer_status, r.abstention_reason) == (0, "self_contained", "answered", None)
    assert [c["chunk_id"] for c in r.citations] == [LODGE.chunk_id] and r.text.endswith("[1]")
    assert len(provider.calls) == 1


def test_result_retrieval():
    worker, client, _, _ = rig()
    job_id = client.submit(Q_LODGE, "s1")
    assert client.result(job_id) is None  # queued: no result yet
    worker.run()
    assert client.result(job_id) is None  # the event is on the queue but not applied yet
    client.poll()
    assert client.result(job_id).turn_index == 0
    assert client.result("unknown") is None and client.status("unknown") is None and client.error("unknown") is None


def test_failed_job_reports_the_error_and_the_worker_goes_on(caplog):
    ctl, _ = controller()
    worker, client, _, _ = rig(Flaky(ctl))
    with caplog.at_level(logging.ERROR, logger="adaptive.jobs.worker"):
        bad = client.submit("boom: What is TU's rule for a multi-bedroom accommodation?", "s1")
        good = client.submit(Q_LODGE, "s1")
        assert worker.run() == 2
    client.poll()
    assert client.tracker.history[bad] == [Q, P, F]
    assert client.error(bad) == {"type": "RuntimeError", "message": "retrieval index unavailable"}
    assert client.result(bad) is None
    assert any("failed" in r.message and r.exc_info for r in caplog.records)  # logged with its traceback
    assert client.tracker.history[good] == [Q, P, C] and client.result(good).turn_index == 0
    assert len(worker.sessions["s1"].turns) == 1  # the failed job added no turn


def test_state_machine_rejects_illegal_transitions_and_ignores_repeats():
    t = JobTracker()
    req = JobRequest.new("s1", "q")
    t.queued(req)
    with pytest.raises(InvalidTransition):
        t.queued(req)  # queued twice
    result = JobResult(turn_index=0, resolution="self_contained", resolution_reason="r", answer_status="answered",
                       text="t", citations=[])
    with pytest.raises(InvalidTransition):
        t.apply(JobEvent.of(req, C, result=result))  # queued -> completed skips processing
    assert t.apply(JobEvent.of(req, P))
    done = JobEvent.of(req, C, result=result)
    assert t.apply(done) and not t.apply(done)  # an identical repeat is ignored
    with pytest.raises(InvalidTransition):
        t.apply(JobEvent.of(req, P))  # nothing after a final state
    with pytest.raises(InvalidTransition):
        t.apply(JobEvent.of(req, F, error={"type": "X", "message": "m"}))
    assert t.history[req.job_id] == [Q, P, C]
    assert not t.apply(JobEvent.of(("other", "s1"), P))  # a job this client did not submit


def test_invalid_requests_fail_the_job_or_are_rejected():
    worker, client, broker, provider = rig()
    req = JobRequest.new("s1", "q")
    client.tracker.queued(req)
    body = json.loads(req.encode())
    del body["question"]
    broker.publish(JOBS_QUEUE, json.dumps(body).encode())
    broker.publish(JOBS_QUEUE, b"not json")
    assert worker.run() == 2
    client.poll()
    assert client.tracker.history[req.job_id] == [Q, F] and client.error(req.job_id)["type"] == "ValidationError"
    assert [q for q, _, _ in broker.rejected] == [JOBS_QUEUE]  # no job_id: logged and dropped by the broker
    assert provider.calls == [] and worker.sessions == {}


def test_duplicate_delivery_runs_the_job_once():
    worker, client, broker, provider = rig()
    job_id = client.submit(Q_LODGE, "s1")
    broker.publish(JOBS_QUEUE, broker.queues[JOBS_QUEUE][0])  # the same request delivered twice
    assert worker.run() == 2
    client.poll()
    assert len(worker.sessions["s1"].turns) == 1 and len(provider.calls) == 1
    finals = [JobEvent.decode(b) for q, b in broker.published if q == EVENTS_QUEUE and JobEvent.decode(b).status == C]
    assert len(finals) == 2 and finals[0] == finals[1]  # the final event republished, identical
    assert client.tracker.history[job_id] == [Q, P, C] and broker.rejected == []


# ---------------------------------------------------------------- phase 3 behind the queue


def test_phase3_session_behaviour_is_unchanged_through_jobs():
    direct, direct_provider = controller()
    session = Session()
    expected = [direct.ask(session, q) for q in (Q_FEB, "Does that apply currently?")]
    worker, client, _, provider = rig()
    jobs = [run_job(worker, client, q, "conv") for q in (Q_FEB, "Does that apply currently?")]
    assert provider.calls == direct_provider.calls  # byte-identical model input
    for job_id, turn in zip(jobs, expected):
        r = client.result(job_id)
        assert (r.resolution, r.answer_status, r.text) == (turn.resolution.kind, turn.status, turn.text)
        assert [c["chunk_id"] for c in r.citations] == turn.cited_chunks
    t1 = worker.sessions["conv"].turns[1]
    assert t1.resolution.kind == "follow_up" and t1.answer.intents[0]["selected_versions"] == {SERIES: ["proc-jul"]}
    assert client.result(jobs[1]).citations[0]["chunk_id"] == JUL_CARD.chunk_id  # retrieved for this turn


def test_sessions_are_kept_apart():
    worker, client, _, _ = rig()
    run_job(worker, client, Q_LODGE, "a")
    b = run_job(worker, client, "Does this policy allow a personal credit card?", "b")
    a = run_job(worker, client, "Does this policy allow a personal credit card?", "a")
    assert client.result(b).resolution == "self_contained" and client.result(b).turn_index == 0  # nothing to resolve
    assert client.result(a).resolution == "follow_up" and client.result(a).turn_index == 1
    assert sorted(worker.sessions) == ["a", "b"]


def test_unresolved_follow_up_is_a_completed_job_with_an_abstention():
    worker, client, _, provider = rig()
    run_job(worker, client, Q_TWO)
    calls = len(provider.calls)
    job_id = run_job(worker, client, "Does that apply currently?")
    r = client.result(job_id)
    assert client.status(job_id) == C and r.answer_status == "abstained" and r.resolution == "unresolved"
    assert r.abstention_reason == "unresolved_reference" and r.citations == [] and len(provider.calls) == calls


# ---------------------------------------------------------------- RabbitMQ (only with a server)


@pytest.mark.skipif(not os.environ.get("RABBITMQ_URL"), reason="RabbitMQ test disabled (set RABBITMQ_URL)")
def test_rabbitmq_round_trip():
    from adaptive.jobs.rabbitmq import RabbitMQBroker

    suffix = uuid.uuid4().hex[:8]
    jobs_q, events_q = f"test.jobs.{suffix}", f"test.events.{suffix}"
    broker = RabbitMQBroker(os.environ["RABBITMQ_URL"])
    try:
        ctl, _ = controller()
        worker = Worker(ctl, broker, jobs_q, events_q)
        client = JobClient(broker, jobs_queue=jobs_q, events_queue=events_q)
        job_id = client.submit(Q_LODGE, "s1")
        assert worker.run(limit=1, timeout=10) == 1
        assert client.wait(job_id, timeout=10) == C
        assert client.tracker.history[job_id] == [Q, P, C]
        assert [c["chunk_id"] for c in client.result(job_id).citations] == [LODGE.chunk_id]
    finally:
        for q in (jobs_q, events_q):
            broker.channel.queue_delete(q)
        broker.close()
