"""Adaptive retrieval controller (AdaptiveRAG phase 1): orchestration above the frozen RAG core.

  controller = AdaptiveController(stack, provider)
  answer = controller.run(query)            # = controller.answer(generation.pipeline.retrieve(stack, query))

  query -> generation.pipeline.retrieve (frozen: hybrid -> temporal -> rerank -> top 10)
        -> decide
        -> GroundedAnswerer.answer (frozen: gate -> context -> LLM -> verification), once or once per version
        -> AdaptiveAnswer

Strategies:
  single            the frozen path, exactly generation.pipeline.answer_retrieval (what `ask()` does).
  split_by_version  one independent grounded answer per version of a document series, each from evidence
                    containing only that version, combined into sections labeled from chunk metadata.

Decision (deterministic; no model, no similarity measure):
  1. eligible  = the question is version-neutral (temporal intent "neutral")
                 AND the assembled context holds two or more versions of one series (context.version_groups)
                 AND those versions' chunks in the context differ (their content_hash sets are not equal).
     A chunk hash covers a whole section, so a hash difference only shows that *something* in the retrieved
     sections differs, not that the rule the question needs differs.
  2. The single path always runs first. If the question is eligible and the verifier rejected that answer
     ONLY for version attribution (the model's quotes are specific to one version: "merges versions",
     "must name that version's effective date", "names ... but cites only"), the relevant evidence differs
     between the versions: the controller discards the rejected answer and answers per version.
     Anything else (accepted answer, other verification problems, abstention, provider error) is final.

Nothing here changes retrieval, the prompt, the verifier or model text. Sections are joined from the parts'
verified claims; the only added text is metadata labels, citation numbers and gap statements.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

from generation import config as C
from generation.answer import NOT_IN_SOURCES_PREFIX, Citation, GroundedAnswer, GroundedAnswerer
from generation.context import Evidence, assemble, version_groups
from generation.pipeline import Retrieval, RetrievalStack, answer_retrieval, retrieve
from generation.prompt import PROMPT_VERSION
from generation.providers import LLMProvider

SINGLE, SPLIT = "single", "split_by_version"
# generation.validate._version_problems messages that mean "a claim relies on version-specific text".
# Pinned by tests/test_adaptive_controller.py against the real verifier.
VERSION_ATTRIBUTION_PROBLEMS = ("merges versions", "must name that version's effective date", "but cites only")


@dataclass
class Decision:
    strategy: str  # single | split_by_version
    reason: str
    signals: dict = field(default_factory=dict)


@dataclass
class AdaptiveAnswer:
    """The final answer, with the GroundedAnswer fields (same meaning) plus the controller's decision and parts."""

    question: str
    status: str  # answered | abstained
    text: str
    decision: Decision
    citations: list[Citation] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    abstention_reason: str | None = None
    abstention_detail: str = ""
    sources_considered: list[dict] = field(default_factory=list)
    model: str | None = None
    provider: str | None = None
    prompt_version: str = PROMPT_VERSION
    raw_output: str | None = None
    verification_problems: list[str] = field(default_factory=list)
    not_in_sources: list[str] = field(default_factory=list)
    verification_notes: list[str] = field(default_factory=list)
    single_attempt: dict | None = None  # the rejected single-path answer when the controller split
    parts: list[dict] = field(default_factory=list)  # split: {"series_id", "doc_id", "label", "answer"}

    def to_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        out = self.text
        if self.status == "answered":
            out += "\n\nSources:\n" + "\n".join(c.render() for c in self.citations)
        return out


def _version_label(chunk) -> str:
    name = " ".join(x for x in (chunk.organization, chunk.title) if x) or chunk.series_id
    return f"{name}, version effective {chunk.effective_date or 'unknown'}"


def evidence_for_version(evidence: list[Evidence], series: str, doc: str) -> list[Evidence]:
    """The evidence a split part sees: every chunk except other versions of `series` (ranks unchanged)."""
    return [e for e in evidence if e.chunk.series_id != series or e.chunk.doc_id == doc]


def _from_grounded(a: GroundedAnswer, decision: Decision) -> AdaptiveAnswer:
    return AdaptiveAnswer(decision=decision, **a.to_dict() | {"citations": a.citations})


class AdaptiveController:
    def __init__(self, stack: RetrievalStack, provider: LLMProvider, answerer=None):
        self.stack, self.provider = stack, provider
        # answerer: None = the frozen GroundedAnswerer(provider); phase 7 passes a drop-in with the same
        # answer(question, evidence, question_versions=...) method (the generation service's client)
        self.answerer = answerer

    def run(self, query: str) -> AdaptiveAnswer:
        return self.answer(retrieve(self.stack, query))

    # ------------------------------------------------------------------ decision

    def eligibility(self, r: Retrieval) -> tuple[str | None, dict]:
        """(series_id to split on, or None; signals). Uses only temporal resolution and context metadata."""
        ctx = assemble(r.evidence)  # identical to the context the answerer assembles
        groups = version_groups(ctx)
        by_label = ctx.by_label()
        versions = {sid: {doc: {"effective_date": by_label[labels[0]].chunk.effective_date, "labels": labels,
                                "content_hashes": sorted({by_label[x].chunk.content_hash[:12] for x in labels})}
                          for doc, labels in docs.items()}
                    for sid, docs in groups.items()}
        differing = [sid for sid, docs in versions.items()
                     if len({tuple(d["content_hashes"]) for d in docs.values()}) > 1]
        intent = r.resolution.intent
        signals = {"intent": intent.kind, "intent_trigger": intent.trigger, "selected_versions": r.question_versions,
                   "temporal_flags": r.resolution.flags, "versions_in_context": versions,
                   "series_with_differing_evidence": differing,
                   "best_rerank_score": max((e.rerank_score for e in r.evidence if e.rerank_score is not None), default=None)}
        if intent.kind != "neutral" or len(differing) != 1:
            return None, signals
        return differing[0], signals

    # ------------------------------------------------------------------ orchestration

    def answer(self, r: Retrieval) -> AdaptiveAnswer:
        series, signals = self.eligibility(r)
        single = (answer_retrieval(self.provider, r) if self.answerer is None  # the frozen path
                  else self.answerer.answer(r.query, r.evidence, question_versions=r.question_versions))
        version_problems = [p for p in single.verification_problems
                            if any(m in p for m in VERSION_ATTRIBUTION_PROBLEMS)]
        signals["single_status"] = single.status
        signals["single_abstention_reason"] = single.abstention_reason
        signals["single_version_problems"] = version_problems
        if series is None:
            reason = self._ineligible_reason(signals)
            return _from_grounded(single, Decision(SINGLE, reason, signals))
        only_version = (single.abstention_reason == "ungrounded_output" and version_problems
                        and len(version_problems) == len(single.verification_problems))
        if not only_version:
            reason = ("eligible (neutral question, versions' evidence differs) but the single answer was "
                      + ("accepted" if single.status == "answered" else
                         f"not rejected for version attribution alone ({single.abstention_reason})"))
            return _from_grounded(single, Decision(SINGLE, reason, signals))
        decision = Decision(SPLIT, "neutral question; versions' evidence differs; the single answer was rejected "
                                   "only for version attribution", signals)
        return self._split(r, series, decision, single)

    @staticmethod
    def _ineligible_reason(s: dict) -> str:
        if s["intent"] != "neutral":
            return f"question selects versions itself (intent {s['intent']})"
        if not s["versions_in_context"]:
            return "no document has two or more versions in the context"
        if not s["series_with_differing_evidence"]:
            return "versions in the context have identical evidence (same content hashes)"
        return "more than one versioned series with differing evidence (not supported)"

    def _split(self, r: Retrieval, series: str, decision: Decision, single: GroundedAnswer) -> AdaptiveAnswer:
        docs = list(decision.signals["versions_in_context"][series])  # ordered by effective date
        answerer = GroundedAnswerer(self.provider) if self.answerer is None else self.answerer
        parts = []
        for doc in docs:
            evidence = evidence_for_version(r.evidence, series, doc)
            chunk = next(e.chunk for e in evidence if e.chunk.doc_id == doc)
            a = answerer.answer(r.query, evidence, question_versions={series: [doc]})
            parts.append({"series_id": series, "doc_id": doc, "label": _version_label(chunk), "answer": a})
        return self._combine(r.query, decision, single, parts)

    @staticmethod
    def _combine(question: str, decision: Decision, single: GroundedAnswer, parts: list[dict]) -> AdaptiveAnswer:
        numbers: dict[str, int] = {}  # chunk_id -> global citation number (first use across parts)
        citations: list[Citation] = []
        claims, sections, gaps, notes, problems, considered = [], [], [], [], [], []
        for p in parts:
            a: GroundedAnswer = p["answer"]
            considered += [s | {"version": p["doc_id"]} for s in a.sources_considered]
            if a.status != "answered":
                sections.append(f"{p['label']}: the retrieved sources for this version do not answer the question.")
                gaps.append(f"{p['label']}: no verified answer ({a.abstention_reason})")
                problems += [f"{p['doc_id']}: {x}" for x in a.verification_problems]
                continue
            local = {c.number: c for c in a.citations}
            sentences = []
            for cl in a.claims:
                marks = []
                for n in cl["citations"]:
                    c = local[n]
                    if c.chunk_id not in numbers:
                        numbers[c.chunk_id] = len(numbers) + 1
                        citations.append(replace(c, number=numbers[c.chunk_id]))
                    marks.append(numbers[c.chunk_id])
                text = cl["text"] if cl["text"].endswith((".", "?", "!")) else cl["text"] + "."
                sentences.append(text + "".join(f"[{n}]" for n in marks))
                claims.append(cl | {"citations": marks, "version": p["doc_id"], "version_label": p["label"]})
            if a.not_in_sources:
                sentences.append(NOT_IN_SOURCES_PREFIX + "; ".join(g.rstrip(".") for g in a.not_in_sources) + ".")
                gaps += [f"{p['label']}: {g}" for g in a.not_in_sources]
            notes += [f"{p['doc_id']}: {x}" for x in a.verification_notes]
            sections.append(f"{p['label']}: " + " ".join(sentences))
        rejected = single.to_dict()
        parts_out = [{k: v for k, v in p.items() if k != "answer"} | {"answer": p["answer"].to_dict()} for p in parts]
        first = parts[0]["answer"]
        common = dict(question=question, decision=decision, sources_considered=considered, model=first.model,
                      provider=first.provider, raw_output=None, single_attempt=rejected, parts=parts_out)
        if not claims:
            reasons = sorted({p["answer"].abstention_reason for p in parts})
            detail = "; ".join(f"{p['label']}: {p['answer'].abstention_reason}: {p['answer'].abstention_detail}"
                               for p in parts)
            return AdaptiveAnswer(status="abstained", text=C.ABSTENTION_TEXT,
                                  abstention_reason=reasons[0] if len(reasons) == 1 else "split_all_parts_abstained",
                                  abstention_detail=detail, verification_problems=problems, **common)
        return AdaptiveAnswer(status="answered", text="\n".join(sections), citations=citations, claims=claims,
                              not_in_sources=gaps, verification_notes=notes, verification_problems=problems, **common)
