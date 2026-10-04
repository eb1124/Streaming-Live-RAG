"""Grounded answering: evidence gate -> context -> LLM -> verification -> citations or abstention.

  answer = GroundedAnswerer(provider).answer(question, evidence, question_versions)
  (generation.pipeline.ask / answer_retrieval supply evidence and question_versions from the integrated retrieval)

Flow:
  1. Evidence-sufficiency gate (no LLM call if it fails):
       - at least MIN_SOURCES source survives context assembly;
       - if cross-encoder scores are present, at least one retrieved candidate scores >= MIN_RERANK_LOGIT.
  2. Assemble numbered sources (generation.context).
  3. One LLM call with the fixed prompt and strict JSON schema (generation.prompt).
  4. Verify the output against the sources (generation.validate).
  5. Accepted and answered -> answer text with [n] markers + citation list. Parts of the question the
     model reported as unanswered by the sources ("not_in_sources") follow the claims under the fixed
     prefix NOT_IN_SOURCES_PREFIX: they describe a gap in the retrieved sources, never a sourced fact.
     Model said insufficient_evidence, or verification failed, or the provider failed -> the fixed
     abstention response. Nothing the model wrote is shown when verification fails.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import config as C
from .context import Context, Evidence, Source, assemble
from .prompt import ANSWER_SCHEMA, PROMPT_VERSION, SYSTEM_PROMPT, user_message
from .providers import LLMProvider, ProviderError
from .validate import verify

NOT_IN_SOURCES_PREFIX = "The retrieved sources do not state: "


@dataclass
class Citation:
    number: int  # [n] marker in the answer text
    label: str  # context label (S1..)
    chunk_id: str
    doc_id: str
    organization: str | None = None
    document: str | None = None
    effective_date: str | None = None
    pages: str = ""
    section: str | None = None
    clause: str | None = None

    def render(self) -> str:
        parts = [p for p in (self.organization, self.document) if p]
        head = " — ".join(parts) if parts else self.doc_id
        if self.effective_date:
            head += f" (effective {self.effective_date})"
        tail = [x for x in (self.section, f"clause {self.clause}" if self.clause else None, self.pages) if x]
        return f"[{self.number}] {head}, " + ", ".join(tail) + f" · chunk {self.chunk_id}"


@dataclass
class GroundedAnswer:
    question: str
    status: str  # answered | abstained
    text: str
    citations: list[Citation] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)  # verified claims with their citation numbers
    abstention_reason: str | None = None  # code: no_evidence | low_relevance | model_insufficient_evidence | ungrounded_output | provider_error
    abstention_detail: str = ""
    sources_considered: list[dict] = field(default_factory=list)
    model: str | None = None
    provider: str | None = None
    prompt_version: str = PROMPT_VERSION
    raw_output: str | None = None
    verification_problems: list[str] = field(default_factory=list)
    not_in_sources: list[str] = field(default_factory=list)  # parts of the question the sources do not answer
    verification_notes: list[str] = field(default_factory=list)  # accepted, recorded (e.g. numbers restated from the question)

    def to_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        if self.status != "answered":
            return self.text
        return self.text + "\n\nSources:\n" + "\n".join(c.render() for c in self.citations)


def make_citation(number: int, s: Source) -> Citation:
    c = s.chunk
    return Citation(
        number=number, label=s.label, chunk_id=c.chunk_id, doc_id=c.doc_id, organization=c.organization,
        document=c.title, effective_date=c.effective_date,
        pages=f"p. {c.page_start}" if c.page_start == c.page_end else f"pp. {c.page_start}-{c.page_end}",
        section=" > ".join(c.section_path) if c.section_path else None, clause=c.clause_id,
    )


def considered(ctx: Context) -> list[dict]:
    return [{"label": s.label, "chunk_id": s.chunk.chunk_id, "retrieval_rank": s.retrieval_rank,
             "duplicate_of": s.duplicate_of} for s in ctx.sources]


class GroundedAnswerer:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def abstain(self, question: str, reason: str, detail: str, ctx: Context | None, **extra) -> GroundedAnswer:
        return GroundedAnswer(question, "abstained", C.ABSTENTION_TEXT, abstention_reason=reason,
                              abstention_detail=detail, sources_considered=considered(ctx) if ctx else [],
                              model=getattr(self.provider, "model", None), provider=getattr(self.provider, "name", None),
                              **extra)

    def answer(self, question: str, evidence: list[Evidence],
               question_versions: dict[str, list[str]] | None = None) -> GroundedAnswer:
        """question_versions: series_id -> doc_ids the question itself selected (temporal resolution); see
        generation.validate. None or {} = no version named, so version attribution is fully required."""
        ctx = assemble(evidence)
        if len(ctx.sources) < C.MIN_SOURCES:
            return self.abstain(question, "no_evidence", "no retrieved chunk could be placed in the context", ctx)
        rerank = [e.rerank_score for e in evidence if e.rerank_score is not None]
        if rerank and max(rerank) < C.MIN_RERANK_LOGIT:
            return self.abstain(question, "low_relevance",
                                f"best cross-encoder score {max(rerank):.2f} < {C.MIN_RERANK_LOGIT}", ctx)
        try:
            resp = self.provider.complete(SYSTEM_PROMPT, user_message(question, ctx), ANSWER_SCHEMA)
        except ProviderError as e:
            return self.abstain(question, "provider_error", str(e), ctx)
        verdict = verify(resp.text, ctx, question, question_versions)
        meta = {"raw_output": resp.text}
        if not verdict.ok:
            return self.abstain(question, "ungrounded_output", "; ".join(verdict.problems), ctx,
                                verification_problems=verdict.problems, **meta)
        if verdict.status == "insufficient_evidence":
            return self.abstain(question, "model_insufficient_evidence", verdict.abstention_reason, ctx, **meta)

        sources = ctx.by_label()
        numbers: dict[str, int] = {}
        citations: list[Citation] = []
        sentences, claims = [], []
        for cl in verdict.claims:
            marks = []
            for label in dict.fromkeys(cl.sources):  # first-use order, no repeats
                if label not in numbers:
                    numbers[label] = len(numbers) + 1
                    citations.append(make_citation(numbers[label], sources[label]))
                marks.append(numbers[label])
            text = cl.text if cl.text.endswith((".", "?", "!")) else cl.text + "."
            sentences.append(text + "".join(f"[{n}]" for n in marks))
            claims.append({"text": cl.text, "citations": marks, "quotes": cl.quotes})
        if verdict.not_in_sources:
            sentences.append(NOT_IN_SOURCES_PREFIX + "; ".join(g.rstrip(".") for g in verdict.not_in_sources) + ".")
        return GroundedAnswer(question, "answered", " ".join(sentences), citations, claims,
                              sources_considered=considered(ctx), model=resp.model, provider=resp.provider,
                              not_in_sources=verdict.not_in_sources, verification_notes=verdict.notes, **meta)
