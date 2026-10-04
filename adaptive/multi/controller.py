"""Multi-intent orchestration (AdaptiveRAG phase 2), above the phase 1 controller and the frozen RAG core.

  controller = MultiIntentController(stack, provider)
  answer = controller.run(query)

  query -> decompose (rules, adaptive.multi.decompose)
     one intent ------------------------------------------------> AdaptiveController.answer(retrieve(query))  delegate
     several -> r0 = retrieve(query); retrieve(sub_query) for each intent            (frozen generation.pipeline)
             -> coverage gate: is every admissible intent's best chunk already in r0's context?
                  yes -> AdaptiveController.answer(r0)                                                     delegate
                  no, every admissible intent selects the same versions as the whole question (compared only
                      for version series present in the intents' evidence)
                      -> fuse -> AdaptiveController.answer(fused retrieval of the whole question)          fused
                  no, the intents select different versions (temporal resolution per intent)
                      -> AdaptiveController.answer(intent retrieval) per admissible intent, in sections   per_intent
             an intent names an organization outside the corpus (organization boundary, decompose.outside_corpus):
                  it is unsupported, the question is never delegated as a whole (its context may hold chunks
                  retrieved for that clause), and the other intents take the fused / per_intent path; with no
                  supported intent left: abstain without a model call                            no_supported_intent

"delegate" is phase 1 exactly: the same retrieval and the same model input (only this record is added). "fused"
shows the model the ORIGINAL question with the fused evidence; the phase 1 controller (single, or split_by_version)
and the unchanged answerer and verifier do the rest, in one call unless phase 1 splits. "per_intent" is needed
because the verifier's version rules depend on which versions the question selected, so intents that select
different versions cannot be verified in one call; each part is answered for its own sub-query.

Intents without admissible evidence contribute nothing and get no model call; they are reported as
"unsupported", never answered. Every claim lists the intents that the chunks it cites serve (fusion.associated).
Nothing here changes retrieval, temporal resolution, the prompt, the verifier or model text; the only added text
(per_intent) is clause labels, citation numbers and gap statements.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

from adaptive.controller import AdaptiveAnswer, AdaptiveController
from generation import config as C
from generation.answer import NOT_IN_SOURCES_PREFIX, Citation
from generation.context import assemble
from generation.pipeline import RETRIEVE_K, Retrieval, RetrievalStack, retrieve
from generation.providers import LLMProvider

from .decompose import Decomposition, Intent, decompose, load_aliases, mentions
from .entity import AlignedRetriever
from .fusion import IntentRetrieval, associated, covered, fuse, fused_retrieval, intents_in, retrieve_intent

DELEGATE, FUSED, PER_INTENT, NO_SUPPORTED_INTENT = "delegate", "fused", "per_intent", "no_supported_intent"


@dataclass
class MultiIntentAnswer:
    """The final answer (GroundedAnswer fields, same meaning) plus the decomposition, the strategy and, per
    intent, its retrieval, coverage and citations."""

    question: str
    status: str  # answered | abstained
    text: str
    strategy: str  # delegate | fused | per_intent | no_supported_intent
    reason: str
    decomposition: dict
    intents: list[dict]
    citations: list[Citation] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)  # each with "intents": the intents it answers
    abstention_reason: str | None = None
    abstention_detail: str = ""
    not_in_sources: list[str] = field(default_factory=list)
    verification_problems: list[str] = field(default_factory=list)
    verification_notes: list[str] = field(default_factory=list)
    evidence_intents: dict[str, list[dict]] = field(default_factory=dict)  # chunk shown to the model -> intents
    phase1: list[AdaptiveAnswer] = field(default_factory=list)  # the phase 1 answer(s) this answer is built from

    @property
    def citation_intents(self) -> dict[int, list[int]]:
        """Citation number -> the intents of the claims that cite it."""
        return {c.number: sorted({k for cl in self.claims if c.number in cl["citations"] for k in cl["intents"]})
                for c in self.citations}

    def to_dict(self) -> dict:
        return asdict(self) | {"citation_intents": self.citation_intents}

    def render(self) -> str:
        out = self.text
        if self.status == "answered":
            out += "\n\nSources:\n" + "\n".join(c.render() for c in self.citations)
        if self.decomposition["multi"]:
            out += "\n\nIntents:\n" + "\n".join(f"  {i['index'] + 1}. {i['text']}  [{i['status']}]" for i in self.intents)
        return out


class MultiIntentController:
    def __init__(self, stack: RetrievalStack, provider: LLMProvider, aliases: dict[str, str] | None = None,
                 retriever=None, answerer=None):
        self.stack, self.provider = stack, provider
        # every retrieval this controller makes; default the frozen generation.pipeline.retrieve (phase 6: a drop-in)
        self.retrieve = retrieve if retriever is None else retriever
        self.phase1 = AdaptiveController(stack, provider, answerer)  # answerer: phase 7 (default: the frozen one)
        self.aliases = load_aliases() if aliases is None else aliases
        self.organizations = {c.organization for c in stack.chunks if c.organization}

    def decompose(self, query: str) -> Decomposition:
        return decompose(query, self.organizations, self.aliases)

    def aligned(self, stack: RetrievalStack, query: str, k: int = RETRIEVE_K) -> Retrieval:
        """`self.retrieve`, entity-aligned (adaptive.multi.entity): a retrieval whose evidence names a different thing
        than the one the query names loses those chunks; any other retrieval is the retriever's, untouched."""
        return AlignedRetriever(self.retrieve, self.organizations, self.aliases)(stack, query, k)

    def answer(self, question: str, r: Retrieval, strategy: str, reason: str) -> MultiIntentAnswer:
        """Phase 1 on a retrieval the caller made (phase 3 refinement: the targeted retrieval of a late constraint),
        for `question` as one intent. Nothing is retrieved here; the record is the one `run` makes for one intent."""
        d = Decomposition(question, [Intent(0, " ".join(question.split()), question,
                                            mentions(question, self.organizations, self.aliases))], False, reason)
        r = replace(r, query=question)  # what the model is asked; the caller records what was retrieved for
        return self._from_phase1(d, strategy, reason, self.phase1.answer(r), [IntentRetrieval(d.intents[0], r)], r)

    def run(self, query: str) -> MultiIntentAnswer:
        d = self.decompose(query)
        r0 = self.aligned(self.stack, query)
        if not d.multi:
            runs = [IntentRetrieval(d.intents[0], r0)]
            return self._from_phase1(d, DELEGATE, f"single intent ({d.reason}): phase 1 unchanged",
                                     self.phase1.answer(r0), runs, r0)
        runs = [retrieve_intent(self.stack, i, self.aligned) for i in d.intents]
        ctx0 = assemble(r0.evidence)  # the context phase 1 assembles for the whole question
        uncovered = [x.intent.index for x in runs if x.admissible and not covered(x, ctx0)]
        # organization boundary: the whole question's context may hold chunks retrieved only because of a clause about
        # an organization outside the corpus, so such a question is never delegated as a whole
        blocked = [x for x in runs if x.intent.outside_corpus]
        if not uncovered and not blocked:
            reason = ("the question's own context already holds every admissible intent's best evidence"
                      if any(x.admissible for x in runs) else "no intent retrieved admissible evidence of its own")
            return self._from_phase1(d, DELEGATE, reason + ": phase 1 unchanged", self.phase1.answer(r0), runs, r0)
        if not any(x.admissible for x in runs):
            return self._no_supported_intent(d, runs, r0)
        why = []
        if uncovered:
            why.append(f"intent(s) {', '.join(str(k + 1) for k in uncovered)} not covered by the question's context")
        if blocked:
            why.append(f"intent(s) {', '.join(str(x.intent.index + 1) for x in blocked)} name an organization outside "
                       f"the corpus ({', '.join(n for x in blocked for n in x.intent.outside_corpus)}): "
                       f"answered from the other intents' evidence only")
        # version selections matter only for series whose chunks the intents actually retrieved
        series = {e.chunk.series_id for x in runs if x.admissible for e in x.retrieval.evidence if e.chunk.series_id}
        relevant = lambda sel: {s: v for s, v in sel.items() if s in series}  # noqa: E731
        selections = {x.intent.index: relevant(x.retrieval.question_versions) for x in runs if x.admissible}
        if all(s == relevant(r0.question_versions) for s in selections.values()):
            fusion = fuse(runs)
            a = self.phase1.answer(fused_retrieval(r0, fusion))
            return self._from_phase1(d, FUSED, "; ".join(why + ["fused per-intent evidence"]), a, runs, r0,
                                     fusion.intents_of)
        why.append(f"the intents select different versions ({ {k + 1: v for k, v in selections.items()} }): "
                   "answered per intent")
        return self._per_intent(d, "; ".join(why), runs, r0)

    # ------------------------------------------------------------------ assembly of the final answer

    def _from_phase1(self, d: Decomposition, strategy: str, reason: str, a: AdaptiveAnswer,
                     runs: list[IntentRetrieval], r0: Retrieval, intents_of: dict | None = None) -> MultiIntentAnswer:
        shown = list(dict.fromkeys(s["chunk_id"] for s in a.sources_considered))
        if intents_of is None:  # only admissible intents can claim evidence (never one outside the corpus)
            evidence_intents = intents_in(shown, [x for x in runs if x.admissible])
        else:
            evidence_intents = {cid: intents_of.get(cid, []) for cid in shown}
        by_number = {c.number: c.chunk_id for c in a.citations}
        claims = [cl | {"intents": sorted({k for n in cl["citations"]
                                           for k in associated(evidence_intents.get(by_number[n], []))})}
                  for cl in a.claims]
        return MultiIntentAnswer(
            question=a.question, status=a.status, text=a.text, strategy=strategy, reason=reason,
            decomposition=d.to_dict(), intents=_intent_records(runs, r0, claims), citations=a.citations,
            claims=claims, abstention_reason=a.abstention_reason, abstention_detail=a.abstention_detail,
            not_in_sources=a.not_in_sources, verification_problems=a.verification_problems,
            verification_notes=a.verification_notes, evidence_intents=evidence_intents, phase1=[a])

    def _no_supported_intent(self, d: Decomposition, runs: list[IntentRetrieval], r0: Retrieval) -> MultiIntentAnswer:
        """Every intent is unsupported and at least one names an organization outside the corpus: no model call
        (the whole question's context would only offer other organizations' evidence)."""
        gaps = [f"Part {x.intent.index + 1} ({x.intent.text}): {_unsupported_text(x)}" for x in runs]
        return MultiIntentAnswer(
            question=d.question, status="abstained", text=C.ABSTENTION_TEXT, strategy=NO_SUPPORTED_INTENT,
            reason="no intent is supported: organizations outside the corpus or no relevant evidence",
            decomposition=d.to_dict(), intents=_intent_records(runs, r0, []),
            abstention_reason="unsupported_organization", abstention_detail="; ".join(gaps), not_in_sources=gaps)

    def _per_intent(self, d: Decomposition, reason: str, runs: list[IntentRetrieval], r0: Retrieval
                    ) -> MultiIntentAnswer:
        numbers: dict[str, int] = {}  # chunk_id -> global citation number (first use across parts)
        citations: list[Citation] = []
        claims, sections, gaps, notes, problems, answers = [], [], [], [], [], []
        evidence_intents: dict[str, list[dict]] = {}
        details = []
        for x in runs:
            k = x.intent.index
            label = f"Part {k + 1} ({x.intent.text})"
            if not x.admissible:
                sections.append(f"{label}: {_unsupported_text(x)}.")
                gaps.append(f"{label}: {_unsupported_text(x)}")
                continue
            a = self.phase1.answer(x.retrieval)
            answers.append(a)
            for s in a.sources_considered:
                evidence_intents.setdefault(s["chunk_id"], []).append(
                    {"intent": k, "rank": s["retrieval_rank"], "score": None})  # shown to intent k's own call
            if a.status != "answered":
                sections.append(f"{label}: the retrieved sources do not answer this part.")
                gaps.append(f"{label}: no verified answer ({a.abstention_reason})")
                problems += [f"intent {k + 1}: {p}" for p in a.verification_problems]
                details.append((a.abstention_reason, f"{label}: {a.abstention_reason}: {a.abstention_detail}"))
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
                prefix = f"{cl['version_label']}: " if cl.get("version_label") else ""  # a phase 1 split part
                sentences.append(prefix + text + "".join(f"[{n}]" for n in marks))
                claims.append(cl | {"citations": marks, "intents": [k]})
            if a.not_in_sources:
                sentences.append(NOT_IN_SOURCES_PREFIX + "; ".join(g.rstrip(".") for g in a.not_in_sources) + ".")
                gaps += [f"{label}: {g}" for g in a.not_in_sources]
            notes += [f"intent {k + 1}: {n}" for n in a.verification_notes]
            sections.append(f"{label}: " + " ".join(sentences))
        common = dict(question=d.question, strategy=PER_INTENT, reason=reason, decomposition=d.to_dict(),
                      intents=_intent_records(runs, r0, claims), verification_problems=problems,
                      evidence_intents=evidence_intents, phase1=answers)
        if not claims:
            reasons = sorted({r for r, _ in details})
            return MultiIntentAnswer(status="abstained", text=C.ABSTENTION_TEXT,
                                     abstention_reason=reasons[0] if len(reasons) == 1 else "per_intent_all_parts_abstained",
                                     abstention_detail="; ".join(t for _, t in details), not_in_sources=gaps, **common)
        return MultiIntentAnswer(status="answered", text="\n".join(sections), citations=citations, claims=claims,
                                 not_in_sources=gaps, verification_notes=notes, **common)


def _unsupported_text(x: IntentRetrieval) -> str:
    if x.intent.outside_corpus:
        return f"this part names an organization outside the corpus ({', '.join(x.intent.outside_corpus)})"
    return "no relevant evidence was retrieved for this part"


def _intent_records(runs: list[IntentRetrieval], r0: Retrieval, claims: list[dict]) -> list[dict]:
    ctx0 = assemble(r0.evidence)
    out = []
    for x in runs:
        k = x.intent.index
        cited = sorted({n for cl in claims if k in cl["intents"] for n in cl["citations"]})
        status = "answered" if cited else ("unsupported" if not x.admissible else "not_answered")
        out.append({"index": k, "text": x.intent.text, "sub_query": x.intent.sub_query,
                    "organizations": x.intent.organizations, "carried": x.intent.carried,
                    "outside_corpus": x.intent.outside_corpus, "topic": x.intent.topic,
                    "temporal_intent": x.retrieval.resolution.intent.kind,
                    "selected_versions": x.retrieval.question_versions, "best_rerank": x.best_rerank,
                    "admissible": x.admissible, "top_chunk": x.top.chunk_id if x.top else None,
                    "covered_by_question_context": covered(x, ctx0),
                    "retrieved": [e.chunk.chunk_id for e in x.retrieval.evidence], "status": status,
                    "citations": cited})
    return out
