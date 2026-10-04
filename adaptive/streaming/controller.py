"""Bounded iterative retrieval (AdaptiveRAG phase 6), above the frozen RAG core.

  controller = StreamingController(stack, provider, max_rounds=3)
  answer = controller.run(question)          # StreamingAnswer: phase 1 answer + retrieval trace

  question -> retrieve(question)                     round 1: generation.pipeline.retrieve, the frozen path
           -> assess coverage                        adaptive/streaming/coverage.py (rules, no model)
              sufficient | structurally unresolved   -> stop
              insufficient -> next refinement         adaptive/streaming/refine.py (new query, same temporal intent)
                              none left, or max_rounds reached -> stop
                           -> retrieve(refinement)  -> merge -> assess again ...
           -> AdaptiveController.answer(retrieval)   phase 1, unchanged: gate, context, model, verifier
  The model is called only after the loop has stopped, never per round.

IterativeRetriever is the loop alone: a drop-in for generation.pipeline.retrieve with the same signature
`(stack, query) -> Retrieval`, so phase 2 (MultiIntentController(retriever=...)) and phase 3
(SessionController(retriever=...)) can use it for every retrieval they make, the jobs worker through them.

Refinement queries only widen the candidates; every judgement is made against the question itself. Merging (after
more than one round; after one round the frozen Retrieval is returned as is):
  * identity is the chunk id: a chunk retrieved again is kept once. Chunks of versions the question's own temporal
    resolution excluded are never retained (refinements keep the temporal intent, so this is only a safeguard).
  * every retained chunk has its cross-encoder score for the question (the existing reranker, reranker.rerank:
    round 1's scores are kept, a new chunk is scored once), and the evidence is ordered by that score, ties in the
    order first retrieved. Coverage, the answerer's relevance gate and the context all use these scores, exactly as
    without the loop; a stronger chunk is never displaced by a weaker one except by the promotion below.
  * promotion: a named organization with no relevant source in the context gets its best-scoring retained chunk
    that is relevant (>= MIN_RERANK_LOGIT for the question) moved into the last context slots; the chunks it
    displaces stay in the retained evidence, only lower. Recorded in the trace.
  * the retained evidence is the top RETRIEVE_K of that order, renumbered; the Retrieval keeps the question, and round
    1's temporal resolution and candidate lists.
"""

from __future__ import annotations

from dataclasses import replace

from adaptive.controller import AdaptiveController
from adaptive.multi.decompose import load_aliases, mentions
from generation import config as C
from generation.context import Evidence, assemble
from generation.pipeline import RETRIEVE_K, RetrievalStack, retrieve
from generation.providers import LLMProvider

from .coverage import assess
from .refine import normalized, refinements, same_temporal_intent
from .state import (INITIAL, STOP_MAX_ROUNDS, STOP_NO_REFINEMENT, STOP_STRUCTURAL, STOP_SUFFICIENT, STRUCTURAL,
                    SUFFICIENT, Round, RetrievalTrace, StreamingAnswer, StreamingRetrieval)

DEFAULT_MAX_ROUNDS = 3  # total retrieval rounds, the first included


def _ids(evidence: list[Evidence]) -> list[str]:
    return [e.chunk.chunk_id for e in evidence]


class IterativeRetriever:
    def __init__(self, max_rounds: int = DEFAULT_MAX_ROUNDS, aliases: dict[str, str] | None = None,
                 observer=None):
        if max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")
        self.max_rounds = max_rounds
        self.aliases = load_aliases() if aliases is None else aliases
        self.observer = observer  # called with every finished RetrievalTrace (optional)

    def __call__(self, stack: RetrievalStack, query: str, k: int = RETRIEVE_K) -> StreamingRetrieval:
        organizations = {c.organization for c in stack.chunks if c.organization}
        named = mentions(query, organizations, self.aliases)
        first = retrieve(stack, query, k)
        allowed = first.resolution.selected  # series -> the only doc ids the question's temporal intent allows
        evidence_of = {e.chunk.chunk_id: e for e in first.evidence}  # retained pool, first-seen order
        score = {cid: e.rerank_score for cid, e in evidence_of.items()}  # cross-encoder score for the question
        evidence = first.evidence
        trace = RetrievalTrace(query, self.max_rounds)
        issued = [normalized(query)]

        def record(q, strategy, target, retrieved, new, dropped, promoted):
            cov = assess(query, evidence, score, first.resolution, organizations, self.aliases)
            trace.rounds.append(Round(len(trace.rounds) + 1, q, strategy, target, retrieved, new, dropped,
                                      _ids(evidence), [s.chunk.chunk_id for s in assemble(evidence).sources],
                                      promoted, cov))
            return cov

        cov = record(query, INITIAL, None, _ids(first.evidence), _ids(first.evidence), [], [])
        while True:
            if cov.decision == SUFFICIENT:
                trace.stop_reason = STOP_SUFFICIENT
                break
            if cov.decision == STRUCTURAL:
                trace.stop_reason = STOP_STRUCTURAL
                break
            if len(trace.rounds) >= self.max_rounds:
                trace.stop_reason = STOP_MAX_ROUNDS
                break
            step = self._next(query, cov, issued, trace)
            if step is None:
                trace.stop_reason = STOP_NO_REFINEMENT
                break
            strategy, target, q = step
            trace.rounds[-1].decision = f"refine: {strategy}" + (f" ({target})" if target else "")
            issued.append(normalized(q))
            r = retrieve(stack, q, k)
            dropped = [e.chunk.chunk_id for e in r.evidence
                       if e.chunk.series_id in allowed and e.chunk.doc_id not in allowed[e.chunk.series_id]]
            new = [e for e in r.evidence if e.chunk.chunk_id not in dropped and e.chunk.chunk_id not in evidence_of]
            for e in new:
                evidence_of[e.chunk.chunk_id] = e
            self._score(stack, query, new, score)
            evidence, promoted = self._merge(evidence_of, score, named, k)
            cov = record(q, strategy, target, _ids(r.evidence), _ids(new), dropped, promoted)
        trace.rounds[-1].decision = f"stop: {trace.stop_reason}"
        if self.observer is not None:
            self.observer(trace)
        if len(trace.rounds) == 1:  # exactly the frozen retrieval
            return StreamingRetrieval(first.query, first.fused, first.resolution, first.pool, first.reranked,
                                      first.evidence, first.seconds, trace)
        return StreamingRetrieval(first.query, first.fused, first.resolution, first.pool, first.reranked, evidence,
                                  dict(first.seconds), trace)

    def _next(self, query, cov, issued, trace):
        """The first refinement that is new and keeps the question's temporal intent; the others are recorded."""
        for strategy, target, q in refinements(query, cov, self.aliases):
            why = None
            if not q.strip():
                why = "empty"
            elif normalized(q) in issued:
                why = "already issued"
            elif not same_temporal_intent(query, q):
                why = "would change the temporal intent"
            if why is None:
                return strategy, target, q
            entry = {"strategy": strategy, "query": q, "why": why}
            if entry not in trace.skipped:
                trace.skipped.append(entry)
        return None

    @staticmethod
    def _score(stack, query, new, score):
        """Score the new chunks against the question itself (None without a reranker)."""
        if not new:
            return
        if stack.reranker is None:
            score.update({e.chunk.chunk_id: None for e in new})
            return
        pos = {c.chunk_id: i for i, c in enumerate(stack.chunks)}
        for r in stack.reranker.rerank(query, [pos[e.chunk.chunk_id] for e in new]):
            score[stack.chunks[r.index].chunk_id] = r.score

    @staticmethod
    def _merge(evidence_of, score, named, k):
        first_seen = {cid: i for i, cid in enumerate(evidence_of)}
        scored = all(score[c] is not None for c in evidence_of)
        order = sorted(evidence_of, key=lambda c: (-score[c], first_seen[c])) if scored else list(evidence_of)

        def relevant(c):
            return score[c] is not None and score[c] >= C.MIN_RERANK_LOGIT

        promoted: list[str] = []
        for org in named:
            base = [c for c in order if c not in promoted]
            window = C.MAX_SOURCES - len(promoted)
            if any(evidence_of[c].chunk.organization == org and relevant(c) for c in base[:window]):
                continue
            candidates = [c for c in base[window:] if evidence_of[c].chunk.organization == org and relevant(c)]
            if candidates and window > 1:  # never promote into the first slot: that stays the best chunk
                promoted.append(candidates[0])  # base is in score order: the best-scoring candidate
        base = [c for c in order if c not in promoted]
        cut = C.MAX_SOURCES - len(promoted)
        final = (base[:cut] + promoted + base[cut:])[:max(k, C.MAX_SOURCES)]
        evidence = [replace(evidence_of[c], rank=i, score=score[c] if scored else evidence_of[c].score,
                            rerank_score=score[c] if scored else evidence_of[c].rerank_score)
                    for i, c in enumerate(final, 1)]
        return evidence, promoted


class StreamingController:
    def __init__(self, stack: RetrievalStack, provider: LLMProvider, max_rounds: int = DEFAULT_MAX_ROUNDS,
                 aliases: dict[str, str] | None = None, observer=None):
        self.stack, self.provider = stack, provider
        self.retriever = IterativeRetriever(max_rounds, aliases, observer)
        self.phase1 = AdaptiveController(stack, provider)

    def run(self, question: str) -> StreamingAnswer:
        r = self.retriever(self.stack, question)
        return StreamingAnswer(question, self.phase1.answer(r), r.trace)
