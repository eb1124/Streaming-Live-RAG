"""Hybrid retrieval: dense (arctic-m) + BM25 fused with Reciprocal Rank Fusion.

  rrf(d) = sum over retrievers r of 1 / (RRF_K + rank_r(d)), for d in the top FUSION_DEPTH of r

RRF_K = 60 is the constant from Cormack et al. (2009); FUSION_DEPTH = 100 of 466 chunks. Both were
fixed before evaluation, not tuned on the benchmark. RRF uses ranks only, so the incomparable score
scales of cosine similarity and BM25 never meet. Ties: higher RRF score, then better best component
rank, then corpus order (deterministic).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from chunking.models import Chunk

from .bm25 import BM25Index
from .dense import DenseIndex, Result, encode, query_text

RRF_K = 60
FUSION_DEPTH = 100


@dataclass
class Fused:
    index: int  # position in the shared chunk list
    score: float
    ranks: dict[str, int] = field(default_factory=dict)  # component retriever -> 1-based rank


def rrf(rankings: dict[str, list[int]], k: int = RRF_K) -> list[Fused]:
    """`rankings`: retriever name -> chunk indices in rank order. Returns fused list, best first."""
    fused: dict[int, Fused] = {}
    for name, ranked in rankings.items():
        for rank, idx in enumerate(ranked, 1):
            f = fused.setdefault(idx, Fused(idx, 0.0))
            f.score += 1.0 / (k + rank)
            f.ranks[name] = rank
    return sorted(fused.values(), key=lambda f: (-f.score, min(f.ranks.values()), f.index))


class HybridRetriever:
    def __init__(self, dense_index: DenseIndex, model, bm25: BM25Index, depth: int = FUSION_DEPTH):
        if [c.chunk_id for c in dense_index.chunks] != [c.chunk_id for c in bm25.chunks]:
            raise ValueError("dense and BM25 indexes must cover the same chunks in the same order")
        self.dense, self.model, self.bm25, self.depth = dense_index, model, bm25, depth
        self.chunks: list[Chunk] = bm25.chunks
        self._pos = {c.chunk_id: i for i, c in enumerate(self.chunks)}

    def fuse(self, query: str) -> list[Fused]:
        vec = encode(self.model, [query_text(query, self.dense.spec)])[0]
        dense_ranked = [self._pos[r.chunk_id] for r in self.dense.search(vec, self.depth)]
        bm25_ranked = [i for i, _ in self.bm25.ranked(query, self.depth)]
        return rrf({"dense": dense_ranked, "bm25": bm25_ranked})

    def retrieve(self, query: str, k: int = 10) -> list[Result]:
        out = []
        for rank, f in enumerate(self.fuse(query)[:k], 1):
            c = self.chunks[f.index]
            out.append(Result(rank, c.chunk_id, f.score, c.doc_id, c.title, c.organization,
                              c.section_path, c.page_start, c.page_end, c.text))
        return out
