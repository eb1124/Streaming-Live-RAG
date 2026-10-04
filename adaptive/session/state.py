"""Explicit session state (AdaptiveRAG phase 3): the turns of one conversation, nothing else.

A Turn keeps what the user asked, how the question was resolved against the session (adaptive.session.resolve),
and the phase 2 answer to the resolved query with its own citations. No evidence is carried from one turn to the
next: every answered turn retrieved and verified its own evidence. `reused_citations` only reports which cited
chunks the anchor turn had also cited.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from adaptive.multi.controller import MultiIntentAnswer

SELF_CONTAINED, FOLLOW_UP, UNRESOLVED = "self_contained", "follow_up", "unresolved"
REFINEMENT = "refinement"  # a late constraint on an answered question: the earlier answer is refined, not redone
# turns that are not a question to the corpus (adaptive.session.gate): never retrieved for
WAIT, SUPPRESS, PRESENTATION = "wait", "suppress", "presentation"
NOT_A_QUESTION = (WAIT, SUPPRESS, PRESENTATION)
UNRESOLVED_TEXT = ("This question refers to earlier context that cannot be resolved safely. Please ask it again with "
                   "the organization and the topic.")
WAITING_TEXT = "Waiting for the rest of the question."  # a state, not an answer; a suppressed turn has no text at all


@dataclass
class Resolution:
    kind: str  # self_contained | follow_up | refinement | unresolved | wait | suppress | presentation
    reason: str
    query: str  # what the multi-intent controller receives ("" when unresolved)
    topic: list[str] = field(default_factory=list)  # the user questions a later follow-up is read with
    temporal: str = ""  # the temporal constraint in effect after this turn ("" = none), as the user wrote it
    organizations: list[str] = field(default_factory=list)  # corpus organizations the turn is about
    anchor: int | None = None  # index of the turn a follow-up was resolved against
    signals: dict = field(default_factory=dict)


@dataclass
class Turn:
    index: int
    question: str  # verbatim
    resolution: Resolution
    answer: MultiIntentAnswer | None  # None: unresolved, wait or suppress (no retrieval, no model call)
    reused_citations: list[str] = field(default_factory=list)  # cited chunks the anchor turn cited as well

    @property
    def status(self) -> str:
        """answered | abstained, or, for a turn that is not a question: waiting | suppressed."""
        if self.answer:
            return self.answer.status
        return {WAIT: "waiting", SUPPRESS: "suppressed"}.get(self.resolution.kind, "abstained")

    @property
    def text(self) -> str:
        if self.answer:
            return self.answer.text
        return {WAIT: WAITING_TEXT, SUPPRESS: ""}.get(self.resolution.kind, UNRESOLVED_TEXT)

    @property
    def abstention_reason(self) -> str | None:
        """Why there is no answer: the answer's own reason, "unresolved_reference", or None (nothing was asked)."""
        if self.answer:
            return self.answer.abstention_reason
        return None if self.resolution.kind in (WAIT, SUPPRESS) else "unresolved_reference"

    @property
    def citations(self) -> list:
        return self.answer.citations if self.answer else []

    @property
    def cited_chunks(self) -> list[str]:
        return [c.chunk_id for c in self.citations]

    @property
    def intents(self) -> int:
        """How many questions this turn asked (0 when unresolved)."""
        if self.answer is None:
            return 0
        return len(self.answer.decomposition["intents"]) if self.answer.decomposition["multi"] else 1

    def to_dict(self) -> dict:
        return {"index": self.index, "question": self.question, "resolution": asdict(self.resolution),
                "status": self.status, "text": self.text, "reused_citations": self.reused_citations,
                "answer": self.answer.to_dict() if self.answer else None}

    def render(self) -> str:
        if self.answer is None:
            return f"{self.text}\n({self.resolution.reason})"
        return self.answer.render()


@dataclass
class Session:
    turns: list[Turn] = field(default_factory=list)

    @property
    def last(self) -> Turn | None:
        return self.turns[-1] if self.turns else None

    @property
    def context(self) -> Turn | None:
        """The last turn that was a question (self-contained, follow-up or unresolved): what a new question is
        resolved against. A waiting, suppressed or presentation turn does not change the topic."""
        return next((t for t in reversed(self.turns) if t.resolution.kind not in NOT_A_QUESTION), None)

    def to_dict(self) -> dict:
        return {"turns": [t.to_dict() for t in self.turns]}
