"""Compose temporal resolution with the existing retrievers (used unchanged, via their public methods).

  hybrid           : hybrid.fuse(query)               -> top k
  hybrid+temporal  : hybrid.fuse(query) -> resolve    -> top k
  rerank           : reranker.ranked(query)           -> top k   (pool = top 20 of hybrid)
  temporal+rerank  : hybrid.fuse(query) -> resolve -> top 20 -> reranker.rerank(query, pool) -> top k

Resolution runs before reranking, so the reranker's fixed-size pool is filled with the requested
version's chunks instead of spending slots on the other version.
"""

from __future__ import annotations

from .resolve import Resolution, TemporalResolver


def hybrid_ranked(stack, query: str) -> list[str]:
    return [stack.chunks[f.index].chunk_id for f in stack.hybrid.fuse(query)]


def hybrid_temporal(stack, resolver: TemporalResolver, query: str) -> tuple[list[str], Resolution]:
    res = resolver.resolve(query, hybrid_ranked(stack, query))
    return res.kept, res


def reranked(stack, query: str) -> list[str]:
    return [stack.chunks[r.index].chunk_id for r in stack.reranker.ranked(query)]


def temporal_reranked(stack, resolver: TemporalResolver, query: str) -> tuple[list[str], Resolution]:
    res = resolver.resolve(query, hybrid_ranked(stack, query))
    pos = {c.chunk_id: i for i, c in enumerate(stack.chunks)}
    pool = [pos[cid] for cid in res.kept[: stack.reranker.pool]]
    return [stack.chunks[r.index].chunk_id for r in stack.reranker.rerank(query, pool)], res
