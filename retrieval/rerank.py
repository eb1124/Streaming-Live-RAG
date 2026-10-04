"""Cross-encoder reranking of hybrid (arctic-m + BM25, RRF) candidates.

  query -> hybrid RRF -> top CANDIDATE_POOL chunks -> cross-encoder score per (query, chunk) -> reorder

Model: cross-encoder/ms-marco-MiniLM-L-6-v2 (pinned revision below).
  * 22.7M parameters (6-layer MiniLM, 384 hidden), about 90 MB of fp32 weights: practical on this CPU-only,
    memory-constrained machine (a 335M model paged heavily here; bge-reranker-base is 278M).
  * Trained for passage reranking on MS MARCO; a public, widely used reference reranker for English.
  * Input: [CLS] query [SEP] passage [SEP], at most 512 word pieces in total (BERT position limit),
    truncated beyond that. With our chunks (<= ~420 tokens) and short queries, nothing should be truncated;
    evaluation counts it.
  * Output: one raw relevance logit per pair (higher = more relevant). Only the order matters.

Fixed defaults, chosen before evaluation and not tuned on the benchmark:
  CANDIDATE_POOL = 20  (must exceed the reported top 10 so reranking can promote evidence from ranks 11-20)
  passage text   = chunk.retrieval_text (the same text dense and BM25 index)
  ties           = broken by the hybrid rank (deterministic)
"""

from __future__ import annotations

from dataclasses import dataclass

from .dense import Result
from .hybrid import HybridRetriever

RERANKER_REPO = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RERANKER_REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"
MAX_LENGTH = 512
CANDIDATE_POOL = 20
BATCH_SIZE = 16


def load_reranker():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(RERANKER_REPO, revision=RERANKER_REVISION, device="cpu", max_length=MAX_LENGTH)


@dataclass
class Reranked:
    index: int  # position in the shared chunk list
    score: float  # cross-encoder logit
    hybrid_rank: int  # 1-based rank in the hybrid candidate pool


class RerankedHybridRetriever:
    def __init__(self, hybrid: HybridRetriever, reranker, pool: int = CANDIDATE_POOL):
        self.hybrid, self.reranker, self.pool = hybrid, reranker, pool
        self.chunks = hybrid.chunks

    def candidates(self, query: str) -> list[int]:
        return [f.index for f in self.hybrid.fuse(query)[: self.pool]]

    def rerank(self, query: str, candidates: list[int]) -> list[Reranked]:
        pairs = [(query, self.chunks[i].retrieval_text) for i in candidates]
        scores = self.reranker.predict(pairs, batch_size=BATCH_SIZE, show_progress_bar=False, convert_to_numpy=True)
        out = [Reranked(i, float(s), r) for r, (i, s) in enumerate(zip(candidates, scores), 1)]
        return sorted(out, key=lambda x: (-x.score, x.hybrid_rank))

    def ranked(self, query: str) -> list[Reranked]:
        return self.rerank(query, self.candidates(query))

    def retrieve(self, query: str, k: int = 10) -> list[Result]:
        out = []
        for rank, r in enumerate(self.ranked(query)[:k], 1):
            c = self.chunks[r.index]
            out.append(Result(rank, c.chunk_id, r.score, c.doc_id, c.title, c.organization,
                              c.section_path, c.page_start, c.page_end, c.text))
        return out
