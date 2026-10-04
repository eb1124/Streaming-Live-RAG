"""Job and event contracts (AdaptiveRAG phase 4): what crosses the queue, validated on both sides.

  client  --JobRequest-->  "adaptiverag.jobs"         -->  worker
  client  <--JobEvent---   "adaptiverag.job_events"   <--  worker   (processing, then completed | failed)

Job lifecycle (the client records "queued" itself when it submits):

  queued -> processing -> completed
  queued -> processing -> failed       (the pipeline raised)
  queued -> failed                     (the worker rejected the request before running it)

The job status says whether the pipeline ran; the answer status says what it found. An abstention (including a
provider error, which the answerer already turns into one) is a COMPLETED job with answer_status "abstained". Only
an exception escaping the session controller makes the job FAILED.

JobResult is a summary of the phase 3 Turn: what a caller needs to show the answer and its citations. Retrieved
evidence, phase 1/2 records and model output stay with the worker's session; they are not copied into events.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator

SCHEMA_VERSION = 1
JOBS_QUEUE = "adaptiverag.jobs"
EVENTS_QUEUE = "adaptiverag.job_events"


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


FINAL = {JobStatus.COMPLETED, JobStatus.FAILED}
TRANSITIONS: dict[JobStatus | None, set[JobStatus]] = {
    None: {JobStatus.QUEUED},
    JobStatus.QUEUED: {JobStatus.PROCESSING, JobStatus.FAILED},
    JobStatus.PROCESSING: {JobStatus.COMPLETED, JobStatus.FAILED},
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: set(),
}


def can_transition(current: JobStatus | None, new: JobStatus) -> bool:
    return new in TRANSITIONS[current]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def encode(self) -> bytes:
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def decode(cls, body: bytes):
        return cls.model_validate_json(body)


class JobRequest(_Message):
    schema_version: int = SCHEMA_VERSION
    job_id: str
    session_id: str  # the conversation; the worker owns its Session
    question: str  # verbatim
    submitted_at: datetime

    @classmethod
    def new(cls, session_id: str, question: str) -> "JobRequest":
        return cls(job_id=uuid.uuid4().hex, session_id=session_id, question=question, submitted_at=_now())


class JobResult(_Message):
    turn_index: int
    resolution: str  # self_contained | follow_up | unresolved | wait | suppress | presentation
    resolution_reason: str
    answer_status: str  # answered | abstained | waiting | suppressed
    text: str
    citations: list[dict]  # generation.answer.Citation fields
    abstention_reason: str | None = None

    @classmethod
    def from_turn(cls, turn) -> "JobResult":
        return cls(turn_index=turn.index, resolution=turn.resolution.kind, resolution_reason=turn.resolution.reason,
                   answer_status=turn.status, text=turn.text, citations=[asdict(c) for c in turn.citations],
                   abstention_reason=turn.abstention_reason)


class JobEvent(_Message):
    schema_version: int = SCHEMA_VERSION
    job_id: str
    session_id: str
    status: JobStatus
    at: datetime
    result: JobResult | None = None  # only on COMPLETED
    error: dict | None = None  # only on FAILED: {"type": ..., "message": ...}

    @model_validator(mode="after")
    def _payload_matches_status(self):
        if (self.result is not None) != (self.status == JobStatus.COMPLETED):
            raise ValueError("a result is required on completed events and allowed on no other")
        if (self.error is not None) != (self.status == JobStatus.FAILED):
            raise ValueError("an error is required on failed events and allowed on no other")
        return self

    @classmethod
    def of(cls, request_or_ids, status: JobStatus, result: JobResult | None = None,
           error: dict | None = None) -> "JobEvent":
        job_id, session_id = request_or_ids if isinstance(request_or_ids, tuple) else (
            request_or_ids.job_id, request_or_ids.session_id)
        return cls(job_id=job_id, session_id=session_id, status=status, at=_now(), result=result, error=error)


def error_of(exc: BaseException) -> dict:
    return {"type": type(exc).__name__, "message": str(exc)}
