"""Job worker (AdaptiveRAG phase 4): consumes JobRequests and runs the phase 3 session controller, unchanged.

  worker = Worker(SessionController(stack, provider), broker)
  worker.run()                      # consume "adaptiverag.jobs" (limit / timeout as in Broker.consume)

Per request: publish PROCESSING, call SessionController.ask(session, question) on the Session of its session_id,
publish COMPLETED with a JobResult, or FAILED with the exception's type and message (logged with its traceback; the
worker goes on with the next job). ask() appends the Turn only after the pipeline returns, so a failed job leaves
its session unchanged. Retrieval, answering and verification happen inside ask(), per turn, as in phase 3.

Sessions are kept in a SessionStore (phase 5, adaptive/session_store): the worker loads the job's session (a new
Session if there is none), runs ask() on it and stores it again after the turn. The default InMemorySessionStore is
phase 4's behaviour; a RedisSessionStore keeps sessions across worker restarts. A store error while loading or saving
fails the job like any other exception (a completed job's turn is always stored). One worker per queue: turns of a
session run in queue order and nothing else writes the session meanwhile.
A request whose job_id this worker already finished (a redelivery) is not run again: its final event is republished,
so a turn is never appended twice. That record is in this worker's memory (see docs/session_store.md, limitations).
"""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from adaptive.session.controller import SessionController
from adaptive.session.state import Session
from adaptive.session_store.store import InMemorySessionStore, SessionStore

from .broker import Broker
from .contracts import EVENTS_QUEUE, JOBS_QUEUE, JobEvent, JobRequest, JobResult, JobStatus, error_of

log = logging.getLogger(__name__)


def _ids(body: bytes) -> tuple[str, str] | None:
    """(job_id, session_id) of an invalid request when it has them, so the failure can be reported."""
    try:
        d = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(d, dict) and isinstance(d.get("job_id"), str) and isinstance(d.get("session_id"), str):
        return d["job_id"], d["session_id"]
    return None


class Worker:
    def __init__(self, controller: SessionController, broker: Broker, jobs_queue: str = JOBS_QUEUE,
                 events_queue: str = EVENTS_QUEUE, sessions: SessionStore | None = None):
        self.controller, self.broker = controller, broker
        self.jobs_queue, self.events_queue = jobs_queue, events_queue
        self.sessions: SessionStore = InMemorySessionStore() if sessions is None else sessions
        self.finished: dict[str, JobEvent] = {}  # job_id -> final event

    def session(self, session_id: str) -> Session:
        """The stored session, or a new (not yet stored) one."""
        return self.sessions.get(session_id) or Session()

    def run(self, limit: int | None = None, timeout: float | None = None) -> int:
        return self.broker.consume(self.jobs_queue, self.handle, limit, timeout)

    def _publish(self, event: JobEvent) -> None:
        self.broker.publish(self.events_queue, event.encode())

    def handle(self, body: bytes) -> None:
        try:
            req = JobRequest.decode(body)
        except ValidationError as e:
            ids = _ids(body)
            if ids is None:
                raise  # not attributable to a job: the broker logs and rejects it
            log.error("invalid job request %s: %s", ids[0], e)
            self._publish(JobEvent.of(ids, JobStatus.FAILED, error=error_of(e)))
            return
        if req.job_id in self.finished:
            log.warning("job %s delivered again; republishing its final event, not running it twice", req.job_id)
            self._publish(self.finished[req.job_id])
            return
        self._publish(JobEvent.of(req, JobStatus.PROCESSING))
        try:
            session = self.session(req.session_id)
            turn = self.controller.ask(session, req.question)
            self.sessions.put(req.session_id, session)
        except Exception as e:
            log.exception("job %s failed", req.job_id)
            final = JobEvent.of(req, JobStatus.FAILED, error=error_of(e))
        else:
            final = JobEvent.of(req, JobStatus.COMPLETED, result=JobResult.from_turn(turn))
        self.finished[req.job_id] = final
        self._publish(final)
