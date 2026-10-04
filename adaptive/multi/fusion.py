"""Per-intent retrieval, coverage gate and evidence fusion (AdaptiveRAG phase 2). No model, no new ranking.

  runs = [retrieve_intent(stack, i) for i in decomposition.intents]   # the frozen generation.pipeline.retrieve
  covered(run, assemble(r0.evidence))                                  # coverage gate against the normal context
  fusion = fuse(runs)                                                  # Fusion(evidence, intents_of)
  r = fused_retrieval(r0, fusion)                                      # a Retrieval for AdaptiveController.answer

retrieve_intent  the retrieval of one intent's sub-query; an intent that names its organizations keeps only their
            chunks from it (the organization boundary between intents).
admissible  the intent names no institution outside the corpus (decompose.outside_corpus: its retrieval would
            only find other organizations' evidence), and its best cross-encoder score is >= MIN_RERANK_LOGIT, the
            answerer's own low-relevance threshold (without a reranker: the intent retrieved anything).
covered     the intent's best chunk, or a chunk with the same text (content_hash), is already in the context that
            the normal retrieval of the whole question assembles (generation.context.assemble, unchanged).
associated  the intents a chunk serves (for citations -> intents): those that scored it >= MIN_RERANK_LOGIT, or
            else the one that scored it highest.
fuse        admissible intents' evidence interleaved by rank (intent 1 rank 1, intent 2 rank 1, intent 1 rank 2,
            ...), each chunk once, with every intent that retrieved it. Ranks are renumbered in that order, so the
            unchanged assemble() (MAX_SOURCES, MAX_CONTEXT_TOKENS) gives every intent a fair share of the context.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from generation import config as C
from generation.context import Context, Evidence
from generation.pipeline import Retrieval, RetrievalStack, retrieve

from .decompose import Intent


@dataclass
class IntentRetrieval:
    intent: Intent
    retrieval: Retrieval

    @property
    def best_rerank(self) -> float | None:
        return max((e.rerank_score for e in self.retrieval.evidence if e.rerank_score is not None), default=None)

    @property
    def admissible(self) -> bool:
        if self.intent.outside_corpus:  # organization boundary: no other organization's evidence stands in for it
            return False
        if not self.retrieval.evidence:
            return False
        best = self.best_rerank
        return best is None or best >= C.MIN_RERANK_LOGIT

    @property
    def top(self):
        """The intent's best chunk (evidence is in rerank order), or None."""
        return self.retrieval.evidence[0].chunk if self.retrieval.evidence else None


@dataclass
class Fusion:
    evidence: list[Evidence]
    intents_of: dict[str, list[dict]] = field(default_factory=dict)  # chunk_id -> [{"intent", "rank"}]


def retrieve_intent(stack: RetrievalStack, intent: Intent, retriever=retrieve) -> IntentRetrieval:
    """`retriever`: generation.pipeline.retrieve, or a drop-in with its signature (phase 6 iterative retrieval).

    Organization boundary between intents: an intent whose own words name organizations keeps, of what its retrieval
    returned, only their chunks (and chunks whose organization is unknown), in the retrieval's order and with its
    ranks and scores. Another organization's chunk is never that intent's evidence, however it ranked, so it cannot
    reach the context or a citation through this intent. An organization carried from another clause is a hint in
    the sub-query only, and an intent that names none keeps everything. The retrieval itself is not changed."""
    r = retriever(stack, intent.sub_query)
    if intent.organizations:
        own = set(intent.organizations)
        kept = [e for e in r.evidence if e.chunk.organization is None or e.chunk.organization in own]
        if len(kept) != len(r.evidence):
            r = replace(r, evidence=kept)
    return IntentRetrieval(intent, r)


def covered(run: IntentRetrieval, ctx: Context) -> bool:
    top = run.top
    if top is None:
        return False
    return any(s.chunk.chunk_id == top.chunk_id or s.chunk.content_hash == top.content_hash for s in ctx.sources)


def fuse(runs: list[IntentRetrieval]) -> Fusion:
    lists = [(x.intent.index, x.retrieval.evidence) for x in runs if x.admissible]
    evidence: list[Evidence] = []
    intents_of: dict[str, list[dict]] = {}
    for depth in range(max((len(ev) for _, ev in lists), default=0)):
        for k, ev in lists:
            if depth >= len(ev):
                continue
            e = ev[depth]
            seen = e.chunk.chunk_id in intents_of
            intents_of.setdefault(e.chunk.chunk_id, []).append(_entry(k, e))
            if not seen:
                evidence.append(replace(e, rank=len(evidence) + 1))
    return Fusion(evidence, intents_of)


def _entry(k: int, e: Evidence) -> dict:
    return {"intent": k, "rank": e.rank, "score": e.rerank_score}


def associated(entries: list[dict]) -> list[int]:
    """The intents a chunk serves: those whose cross-encoder score for it is >= MIN_RERANK_LOGIT (relevant to that
    intent); if none, the intent that scored it highest. Without scores (no reranker): every intent that retrieved it."""
    if not entries:
        return []
    if any(x["score"] is None for x in entries):
        return sorted({x["intent"] for x in entries})
    relevant = {x["intent"] for x in entries if x["score"] >= C.MIN_RERANK_LOGIT}
    if relevant:
        return sorted(relevant)
    best = max(x["score"] for x in entries)
    return sorted({x["intent"] for x in entries if x["score"] == best})


def fused_retrieval(r0: Retrieval, fusion: Fusion) -> Retrieval:
    """The whole question with the fused evidence. The temporal resolution is the whole question's: the controller
    uses the fused path only when every intent selected the same versions as the whole question."""
    return Retrieval(r0.query, r0.fused, r0.resolution, [], [], fusion.evidence, dict(r0.seconds))


def intents_in(chunk_ids: list[str], runs: list[IntentRetrieval]) -> dict[str, list[dict]]:
    """chunk_id -> the intents whose own retrieval (top k) returned that chunk, with its rank and score there."""
    return {cid: [_entry(x.intent.index, e) for x in runs for e in x.retrieval.evidence if e.chunk.chunk_id == cid]
            for cid in chunk_ids}
