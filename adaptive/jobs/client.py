"""Job client (AdaptiveRAG phase 4): submits questions as jobs and follows their state from the worker's events.

  client = JobClient(broker)
  job_id = client.submit("Does that apply currently?", session_id="s1")   # state: queued
  client.poll()                                                           # apply events: processing, completed
  client.status(job_id), client.result(job_id), client.error(job_id)

JobTracker applies events under the lifecycle in adaptive/jobs/contracts.py: a transition the lifecycle does not
allow raises InvalidTransition (the broker then logs and rejects the event); an event identical to the job's last one
(a republished final event) is ignored. Events of jobs this client did not submit are ignored with a warning.
Phase 4 assumes one client per events queue.
"""

from __future__ import annotations

import logging
import time

from .broker import Broker
from .contracts import (EVENTS_QUEUE, FINAL, JOBS_QUEUE, JobEvent, JobRequest, JobResult, JobStatus, can_transition,
                        error_of)

log = logging.getLogger(__name__)


class InvalidTransition(ValueError):
    pass


class JobTracker:
    def __init__(self):
        self.history: dict[str, list[JobStatus]] = {}
        self.events: dict[str, list[JobEvent]] = {}

    def status(self, job_id: str) -> JobStatus | None:
        h = self.history.get(job_id)
        return h[-1] if h else None

    def _set(self, job_id: str, status: JobStatus) -> None:
        current = self.status(job_id)
        if not can_transition(current, status):
            raise InvalidTransition(f"job {job_id}: {current.value if current else 'new'} -> {status.value}")
        self.history.setdefault(job_id, []).append(status)

    def queued(self, request: JobRequest) -> None:
        self._set(request.job_id, JobStatus.QUEUED)

    def apply(self, event: JobEvent) -> bool:
        """Record the event; False when it is ignored (unknown job, or a repeat of the last event)."""
        if event.job_id not in self.history:
            log.warning("event for job %s, which this client did not submit: ignored", event.job_id)
            return False
        seen = self.events.setdefault(event.job_id, [])
        if seen and seen[-1] == event:
            return False
        self._set(event.job_id, event.status)
        seen.append(event)
        return True

    def handle(self, body: bytes) -> None:
        self.apply(JobEvent.decode(body))

    def final(self, job_id: str) -> JobEvent | None:
        seen = self.events.get(job_id)
        return seen[-1] if seen and seen[-1].status in FINAL else None


class JobClient:
    def __init__(self, broker: Broker, tracker: JobTracker | None = None, jobs_queue: str = JOBS_QUEUE,
                 events_queue: str = EVENTS_QUEUE):
        self.broker, self.tracker = broker, tracker or JobTracker()
        self.jobs_queue, self.events_queue = jobs_queue, events_queue

    def submit(self, question: str, session_id: str) -> str:
        req = JobRequest.new(session_id, question)
        self.tracker.queued(req)
        try:
            self.broker.publish(self.jobs_queue, req.encode())
        except Exception as e:  # never left "queued" for a job that was not sent
            self.tracker.apply(JobEvent.of(req, JobStatus.FAILED, error=error_of(e)))
            raise
        return req.job_id

    def poll(self, limit: int | None = None, timeout: float | None = None) -> int:
        return self.broker.consume(self.events_queue, self.tracker.handle, limit, timeout)

    def wait(self, job_id: str, timeout: float = 60.0) -> JobStatus | None:
        """Apply events until the job is final or `timeout` seconds pass (the in-memory broker returns at once)."""
        deadline = time.monotonic() + timeout
        while self.status(job_id) not in FINAL:
            left = deadline - time.monotonic()
            if left <= 0 or self.poll(limit=1, timeout=left) == 0:
                break
        return self.status(job_id)

    def status(self, job_id: str) -> JobStatus | None:
        return self.tracker.status(job_id)

    def result(self, job_id: str) -> JobResult | None:
        e = self.tracker.final(job_id)
        return e.result if e else None

    def error(self, job_id: str) -> dict | None:
        e = self.tracker.final(job_id)
        return e.error if e else None
