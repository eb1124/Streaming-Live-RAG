"""Glue: existing retrieval and temporal resolution (unchanged) -> Evidence list -> grounded answer.

The integrated pipeline (`retrieve`, used by `ask`, the CLI `python -m generation` and the integration
harness evaluation/integration/run.py):

  query -> dense + BM25 -> RRF (retrieval.hybrid)
        -> temporal / version resolution (temporal.resolve; stable filter of the fused list)
        -> top CANDIDATE_POOL -> cross-encoder rerank (retrieval.rerank)
        -> top RETRIEVE_K -> Evidence (rank order)
        -> generation.answer.GroundedAnswerer (told which versions the question selected)

This is the composition of temporal/pipeline.py (temporal_reranked), keeping scores for the answerer.

`retrieve_evidence` is the pre-temporal path (hybrid -> rerank, no version resolution). It is kept only
because the generation evidence suite (evaluation/generation) was defined and recorded on it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .answer import GroundedAnswer, GroundedAnswerer
from .context import Evidence
from .providers import LLMProvider

RETRIEVE_K = 10  # candidates handed to context assembly (it keeps at most MAX_SOURCES)


@dataclass
class RetrievalStack:
    chunks: list
    hybrid: object
    reranker: object | None = None  # retrieval.rerank.RerankedHybridRetriever
    resolver: object | None = None  # temporal.resolve.TemporalResolver


@dataclass
class Retrieval:
    """Every stage of one integrated retrieval, for callers that record them (the integration harness)."""

    query: str
    fused: list  # retrieval.hybrid fused results, full list
    resolution: object  # temporal.resolve.Resolution
    pool: list[int]  # chunk indices handed to the reranker (post-temporal)
    reranked: list  # retrieval.rerank.Reranked for the pool (empty without a reranker)
    evidence: list[Evidence]
    seconds: dict = field(default_factory=dict)  # hybrid_retrieval_rrf / temporal / rerank

    @property
    def question_versions(self) -> dict[str, list[str]]:
        """series_id -> doc_ids the question itself selected (empty for version-neutral questions)."""
        return self.resolution.selected


def load_stack(rerank: bool = True) -> RetrievalStack:
    from chunking.pipeline import load_chunks
    from retrieval.bm25 import BM25Index
    from retrieval.dense import DenseIndex, load_model
    from retrieval.embedders import CANDIDATES
    from retrieval.hybrid import HybridRetriever
    from temporal.resolve import TemporalResolver

    chunks = [c for cs in load_chunks().values() for c in cs]
    model = load_model(CANDIDATES["arctic-m"])
    dense = DenseIndex.load("arctic-m", chunks)
    hybrid = HybridRetriever(dense, model, BM25Index(dense.chunks))
    rr = None
    if rerank:
        from retrieval.rerank import RerankedHybridRetriever, load_reranker

        rr = RerankedHybridRetriever(hybrid, load_reranker())
    return RetrievalStack(hybrid.chunks, hybrid, rr, TemporalResolver.from_chunks(hybrid.chunks))


def retrieve(stack: RetrievalStack, query: str, k: int = RETRIEVE_K) -> Retrieval:
    """The integrated path: hybrid -> temporal resolution -> rerank pool -> rerank -> top k."""
    chunks = stack.chunks
    seconds = {}
    t = time.perf_counter()
    fused = stack.hybrid.fuse(query)
    seconds["hybrid_retrieval_rrf"] = time.perf_counter() - t

    t = time.perf_counter()
    res = stack.resolver.resolve(query, [chunks[f.index].chunk_id for f in fused])
    seconds["temporal"] = time.perf_counter() - t
    pos = {c.chunk_id: i for i, c in enumerate(chunks)}

    if stack.reranker is None:
        by_index = {f.index: f for f in fused}
        kept = [pos[c] for c in res.kept[:k]]
        evidence = [Evidence(chunks[i], rank, by_index[i].score, "hybrid-rrf-temporal") for rank, i in enumerate(kept, 1)]
        return Retrieval(query, fused, res, kept, [], evidence, seconds)

    pool = [pos[c] for c in res.kept[: stack.reranker.pool]]
    t = time.perf_counter()
    rr = stack.reranker.rerank(query, pool)
    seconds["rerank"] = time.perf_counter() - t
    evidence = [Evidence(chunks[r.index], rank, r.score, "hybrid-rrf-temporal-rerank", rerank_score=r.score)
                for rank, r in enumerate(rr[:k], 1)]
    return Retrieval(query, fused, res, pool, rr, evidence, seconds)


def retrieve_evidence(stack: RetrievalStack, query: str, k: int = RETRIEVE_K) -> list[Evidence]:
    """Pre-temporal path (no version resolution); used only by the generation evidence suite."""
    if stack.reranker is not None:
        ranked = stack.reranker.ranked(query)[:k]
        return [Evidence(stack.chunks[r.index], rank, r.score, "hybrid-rrf-rerank", rerank_score=r.score)
                for rank, r in enumerate(ranked, 1)]
    fused = stack.hybrid.fuse(query)[:k]
    return [Evidence(stack.chunks[f.index], rank, f.score, "hybrid-rrf") for rank, f in enumerate(fused, 1)]


def answer_retrieval(provider: LLMProvider, r: Retrieval) -> GroundedAnswer:
    return GroundedAnswerer(provider).answer(r.query, r.evidence, question_versions=r.question_versions)


def ask(stack: RetrievalStack, provider: LLMProvider, query: str) -> GroundedAnswer:
    return answer_retrieval(provider, retrieve(stack, query))
