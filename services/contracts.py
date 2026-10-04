"""Service contracts (phase 7): every request and response that crosses a service boundary, validated on both sides.

  client  --QueryRequest-->       api  --JobRequest (phase 4)-->  RabbitMQ  -->  orchestrator worker
  client  <--QueryResponse--      api  <--JobEvent  (phase 4)--  RabbitMQ  <--  orchestrator worker
  orchestrator --RetrievalRequest-->  retrieval   --RetrievalResponse-->  orchestrator
  orchestrator --GenerationRequest--> generation  --GenerationResponse--> orchestrator

Pydantic models; unknown fields are rejected; JSON via model_dump_json / model_validate_json. The job messages between
the api and the orchestrator are the phase 4 contracts (adaptive/jobs/contracts.py), unchanged. The payloads reuse
the existing types, so nothing is re-described: a chunk is chunking.models.Chunk, an answer is
generation.answer.GroundedAnswer, a phase 6 trace is adaptive.streaming.state.RetrievalTrace.

Correlation: request_id (made by the api, returned to the client), job_id (the phase 4 job: the id every downstream
call carries), session_id (the conversation). Responses echo the ids of their request.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from adaptive.streaming.state import RetrievalTrace, StreamingRetrieval
from chunking.models import Chunk
from generation.answer import GroundedAnswer
from generation.context import Evidence
from generation.pipeline import RETRIEVE_K, Retrieval
from temporal.intent import DateRef, TemporalIntent
from temporal.resolve import Resolution

SCHEMA_VERSION = 1
_ID = Field(default=None, max_length=128)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def encode(self) -> bytes:
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def decode(cls, body: bytes | str):
        return cls.model_validate_json(body)


class _Correlated(_Contract):
    schema_version: int = SCHEMA_VERSION
    request_id: str | None = _ID
    job_id: str | None = _ID
    session_id: str | None = _ID

    def ids(self) -> dict:
        return {"request_id": self.request_id, "job_id": self.job_id, "session_id": self.session_id}


# ---------------------------------------------------------------- evidence and resolution on the wire


class EvidenceItem(_Contract):
    """generation.context.Evidence: the chunk (every metadata field: document, pages, section, clause, versions, ...),
    its rank, and its scores."""

    chunk: Chunk
    rank: int
    score: float | None = None
    retriever: str = ""
    rerank_score: float | None = None

    @classmethod
    def of(cls, e: Evidence) -> "EvidenceItem":
        return cls(chunk=e.chunk, rank=e.rank, score=e.score, retriever=e.retriever, rerank_score=e.rerank_score)

    def evidence(self) -> Evidence:
        return Evidence(self.chunk, self.rank, self.score, self.retriever, self.rerank_score)


class DateInfo(_Contract):
    year: int
    month: int
    day: int | None
    text: str


class ResolutionInfo(_Contract):
    """temporal.resolve.Resolution without the full candidate lists (`kept`: every candidate id; they stay in the
    retrieval service, and nothing above retrieval reads them)."""

    kind: str  # neutral | point_in_time | compare | current
    dates: list[DateInfo] = []
    trigger: str = ""
    selected: dict[str, list[str]] = {}
    dropped: list[str] = []
    flags: list[str] = []
    candidates: int = 0  # how many candidates the resolution saw

    @classmethod
    def of(cls, r: Resolution) -> "ResolutionInfo":
        i = r.intent
        return cls(kind=i.kind, dates=[DateInfo(year=d.year, month=d.month, day=d.day, text=d.text) for d in i.dates],
                   trigger=i.trigger, selected=r.selected, dropped=r.dropped, flags=r.flags,
                   candidates=len(r.kept) + len(r.dropped))

    def resolution(self) -> Resolution:
        intent = TemporalIntent(self.kind, [DateRef(d.year, d.month, d.day, d.text) for d in self.dates], self.trigger)
        return Resolution(intent, {k: list(v) for k, v in self.selected.items()}, [], list(self.dropped),
                          list(self.flags))


# ---------------------------------------------------------------- retrieval


class RetrievalRequest(_Correlated):
    query: str = Field(min_length=1, max_length=4000)
    mode: Literal["single", "iterative"] = "single"  # iterative: the phase 6 loop
    max_rounds: int = Field(default=3, ge=1, le=10)
    k: int = Field(default=RETRIEVE_K, ge=1, le=50)


class RetrievalResponse(_Correlated):
    query: str
    mode: Literal["single", "iterative"]
    evidence: list[EvidenceItem]
    resolution: ResolutionInfo
    trace: RetrievalTrace | None = None  # phase 6 loop trace (iterative mode)
    seconds: dict[str, float] = {}

    @classmethod
    def of(cls, req: RetrievalRequest, r: Retrieval) -> "RetrievalResponse":
        return cls(**req.ids(), query=r.query, mode=req.mode, evidence=[EvidenceItem.of(e) for e in r.evidence],
                   resolution=ResolutionInfo.of(r.resolution), trace=getattr(r, "trace", None),
                   seconds={k: float(v) for k, v in r.seconds.items()})

    def retrieval(self) -> Retrieval:
        """The Retrieval phases 1 to 3 consume. The hybrid / pool / rerank candidate lists stay in the service."""
        evidence = [e.evidence() for e in self.evidence]
        args = (self.query, [], self.resolution.resolution(), [], [], evidence, dict(self.seconds))
        return StreamingRetrieval(*args, trace=self.trace) if self.trace is not None else Retrieval(*args)


# ---------------------------------------------------------------- generation


class GenerationRequest(_Correlated):
    question: str = Field(min_length=1, max_length=8000)
    evidence: list[EvidenceItem]
    question_versions: dict[str, list[str]] | None = None


class GenerationResponse(_Correlated):
    answer: GroundedAnswer


# ---------------------------------------------------------------- the public query


class QueryRequest(_Contract):
    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    question: str = Field(min_length=1, max_length=2000)
    request_id: str | None = Field(default=None, max_length=128)


class ResolutionView(_Contract):
    kind: str  # self_contained | follow_up | unresolved | wait | suppress | presentation
    reason: str
    query: str  # what the pipeline received (the rewritten query of a follow-up; "" when nothing was retrieved)
    temporal: str  # the temporal constraint in effect
    organizations: list[str]
    anchor: int | None


class QueryResponse(_Contract):
    schema_version: int = SCHEMA_VERSION
    request_id: str
    job_id: str
    session_id: str
    status: Literal["completed", "failed"]
    error: dict | None = None
    turn_index: int | None = None
    answer_status: str | None = None  # answered | abstained | waiting | suppressed
    text: str | None = None
    citations: list[dict] = []  # generation.answer.Citation fields
    abstention_reason: str | None = None
    resolution: ResolutionView | None = None
    strategy: str | None = None  # phase 2: delegate | fused | per_intent | no_supported_intent
    answer_strategies: list[str] = []  # phase 1, per answer: single | split_by_version
    evidence: list[str] = []  # chunk ids shown to the model
    reused_citations: list[str] = []

    @classmethod
    def of_turn(cls, request_id: str, job_id: str, session_id: str, turn) -> "QueryResponse":
        from dataclasses import asdict

        r, a = turn.resolution, turn.answer
        return cls(request_id=request_id, job_id=job_id, session_id=session_id, status="completed",
                   turn_index=turn.index, answer_status=turn.status, text=turn.text,
                   citations=[asdict(c) for c in turn.citations],
                   abstention_reason=turn.abstention_reason,
                   resolution=ResolutionView(kind=r.kind, reason=r.reason, query=r.query, temporal=r.temporal,
                                             organizations=r.organizations, anchor=r.anchor),
                   strategy=a.strategy if a else None,
                   answer_strategies=[p.decision.strategy for p in a.phase1] if a else [],
                   evidence=list(dict.fromkeys(s["chunk_id"] for p in (a.phase1 if a else [])
                                               for s in p.sources_considered)),
                   reused_citations=turn.reused_citations)
