"""Orchestrator client (phase 7), used by the api: a QueryRequest becomes a phase 4 job, and the job's outcome and the
stored turn become a QueryResponse.

  client = OrchestratorClient(broker_factory, sessions, timeout_s)
  response = client.query(QueryRequest(session_id=..., question=...))

  submit JobRequest (adaptive.jobs.client.JobClient)  ->  wait for the job's final JobEvent
  completed: read the turn from the session store (the orchestrator stored it) -> QueryResponse.of_turn
  failed:    QueryResponse with the job's error
  no final event within the timeout: QueryTimeout

The job result (phase 4 JobResult) names the turn; everything else in the response (rewritten query, temporal
constraint, strategy, evidence, citations) comes from the stored session, the phase 5 store the orchestrator writes:
nothing is re-derived here and the job contracts are unchanged.

One broker connection per query (a pika BlockingConnection is neither thread-safe nor kept alive while idle), and
one query at a time per client: phase 4 assumes one consumer of the events queue.
`pump` runs after submitting (the in-process deployment of the tests: the worker's run()); None when the orchestrator
is its own process.
"""

from __future__ import annotations

import threading
import uuid

from adaptive.jobs.client import JobClient
from adaptive.jobs.contracts import JobStatus
from adaptive.session_store.store import SessionStore

from .. import telemetry
from ..contracts import QueryRequest, QueryResponse


class QueryTimeout(RuntimeError):
    pass


class OrchestratorClient:
    def __init__(self, broker_factory, sessions: SessionStore, timeout_s: float = 180.0, pump=None):
        self.broker_factory, self.sessions, self.timeout_s, self.pump = broker_factory, sessions, timeout_s, pump
        self._lock = threading.Lock()

    def query(self, req: QueryRequest) -> QueryResponse:
        request_id = req.request_id or uuid.uuid4().hex
        ids = {"request_id": request_id, "session_id": req.session_id}
        telemetry.annotate(ids=ids)  # the api's HTTP span
        with self._lock:
            broker = self.broker_factory()
            try:
                jobs = JobClient(broker)
                job_id = jobs.submit(req.question, req.session_id)
                telemetry.annotate(ids={**ids, "job_id": job_id})
                if self.pump is not None:
                    self.pump()
                status = jobs.wait(job_id, self.timeout_s)
                result, error = jobs.result(job_id), jobs.error(job_id)
            finally:
                close = getattr(broker, "close", None)
                if close is not None:
                    close()
        if status == JobStatus.FAILED:
            return QueryResponse(request_id=request_id, job_id=job_id, session_id=req.session_id, status="failed",
                                 error=error)
        if status != JobStatus.COMPLETED:
            raise QueryTimeout(f"job {job_id}: no final event within {self.timeout_s:g} s (state {status})")
        session = self.sessions.get(req.session_id)
        if session is None or len(session.turns) <= result.turn_index:
            raise RuntimeError(f"job {job_id} completed turn {result.turn_index}, but the session store does not have it")
        turn = session.turns[result.turn_index]
        return QueryResponse.of_turn(request_id, job_id, req.session_id, turn)

    def session(self, session_id: str) -> dict | None:
        """The stored session as the phase 3 Session.to_dict() gives it, or None (phase 10B: read only; the api's
        GET /sessions/{session_id}). Nothing is computed here: it is what the orchestrator stored."""
        stored = self.sessions.get(session_id)
        return None if stored is None else stored.to_dict()
