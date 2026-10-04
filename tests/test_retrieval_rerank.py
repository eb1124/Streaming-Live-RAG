"""Reranking stage: pool construction, ordering, tie-breaks (fake cross-encoder, no model download)."""

from types import SimpleNamespace

from retrieval.hybrid import Fused
from retrieval.rerank import CANDIDATE_POOL, RerankedHybridRetriever


def chunk(i):
    return SimpleNamespace(chunk_id=f"c{i}", doc_id="d", title="t", organization="o", section_path=[], page_start=1,
                           page_end=1, text=f"text {i}", retrieval_text=f"text {i}")


class FakeHybrid:
    def __init__(self, n):
        self.chunks = [chunk(i) for i in range(n)]

    def fuse(self, query):  # hybrid order: 0, 1, 2, ...
        return [Fused(i, 1.0 / (60 + i + 1), {"dense": i + 1}) for i in range(len(self.chunks))]


class FakeCrossEncoder:
    def __init__(self, scores):
        self.scores, self.calls = scores, []

    def predict(self, pairs, **kw):
        self.calls.append(pairs)
        return [self.scores.get(p[1], 0.0) for p in pairs]


def test_pool_is_fixed_size_prefix_of_hybrid():
    rr = RerankedHybridRetriever(FakeHybrid(50), FakeCrossEncoder({}))
    assert CANDIDATE_POOL == 20
    assert rr.candidates("q") == list(range(20))


def test_rerank_orders_by_score_and_breaks_ties_by_hybrid_rank():
    scores = {"text 7": 5.0, "text 3": 2.0, "text 0": 2.0}  # 0 and 3 tie; everything else 0.0
    ce = FakeCrossEncoder(scores)
    rr = RerankedHybridRetriever(FakeHybrid(30), ce)
    ranked = rr.ranked("q")
    assert [r.index for r in ranked[:4]] == [7, 0, 3, 1]  # 0 before 3 (better hybrid rank); then 1, 2, ...
    assert ranked[0].hybrid_rank == 8 and len(ranked) == 20
    assert len(ce.calls) == 1 and ce.calls[0][0] == ("q", "text 0")  # (query, retrieval_text) pairs


def test_retrieve_returns_top_k_with_scores_and_is_deterministic():
    rr = RerankedHybridRetriever(FakeHybrid(30), FakeCrossEncoder({"text 12": 3.0}))
    a = rr.retrieve("q", k=5)
    b = rr.retrieve("q", k=5)
    assert [r.chunk_id for r in a] == [r.chunk_id for r in b] == ["c12", "c0", "c1", "c2", "c3"]
    assert a[0].rank == 1 and a[0].score == 3.0


def test_evidence_outside_pool_cannot_be_recovered():
    rr = RerankedHybridRetriever(FakeHybrid(30), FakeCrossEncoder({"text 25": 99.0}))
    assert "c25" not in [r.chunk_id for r in rr.retrieve("q", k=10)]
