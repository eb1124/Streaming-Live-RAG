"""Per-turn state of the iterative retrieval loop (AdaptiveRAG phase 6): the coverage decision of each round and the
trace of the whole loop. Execution state only: it is returned with the retrieval (StreamingRetrieval.trace), never
stored in the phase 3 session. No timings are recorded here, so identical inputs give an identical trace.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from generation.pipeline import Retrieval

SUFFICIENT, INSUFFICIENT, STRUCTURAL = "sufficient", "insufficient", "structurally_unresolved"
# stopping reasons
STOP_SUFFICIENT = "sufficient"
STOP_STRUCTURAL = "structurally_unresolved"
STOP_NO_REFINEMENT = "no_new_refinement"
STOP_MAX_ROUNDS = "max_rounds"

INITIAL, ORGANIZATION_FOCUS, KEYWORD_FOCUS = "initial", "organization_focus", "keyword_focus"


@dataclass
class Coverage:
    decision: str  # sufficient | insufficient | structurally_unresolved
    reason: str
    signals: dict = field(default_factory=dict)  # see adaptive/streaming/coverage.py


@dataclass
class Round:
    iteration: int  # 1-based
    query: str  # what retrieve() received
    strategy: str  # initial | organization_focus | keyword_focus
    target: str | None  # the organization an organization_focus round is about
    retrieved: list[str]  # chunk ids retrieve() returned, in rank order
    new: list[str]  # of those, not retained by an earlier round
    excluded_versions: list[str]  # of those, versions the question itself excluded (never retained)
    retained: list[str]  # the accumulated evidence after this round, in rank order
    context: list[str]  # what the model would be shown from it (generation.context.assemble)
    promoted: list[str]  # chunks moved into the context to close an organization gap
    coverage: Coverage
    decision: str = ""  # "refine: <strategy>" or "stop: <reason>"


@dataclass
class RetrievalTrace:
    question: str
    max_rounds: int
    rounds: list[Round] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # refinements not issued: {"strategy", "query", "why"}
    stop_reason: str = ""

    @property
    def iterations(self) -> int:
        return len(self.rounds)

    @property
    def queries(self) -> list[str]:
        return [r.query for r in self.rounds]

    @property
    def final(self) -> Round:
        return self.rounds[-1]

    def to_dict(self) -> dict:
        return asdict(self) | {"iterations": self.iterations, "queries": self.queries}


@dataclass
class StreamingRetrieval(Retrieval):
    """A Retrieval (the frozen type, unchanged) with the loop's trace. After one round it holds exactly what
    generation.pipeline.retrieve returned; after several, the accumulated evidence (see controller.py)."""

    trace: RetrievalTrace | None = None


@dataclass
class StreamingAnswer:
    question: str
    answer: object  # adaptive.controller.AdaptiveAnswer
    trace: RetrievalTrace

    def to_dict(self) -> dict:
        return {"question": self.question, "answer": self.answer.to_dict(), "trace": self.trace.to_dict()}
