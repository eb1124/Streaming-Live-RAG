"""BM25 analyzer/scoring and RRF fusion: definitions, determinism, no dependence on models."""

import math
from types import SimpleNamespace

import pytest

from retrieval.bm25 import BM25Index, analyze
from retrieval.hybrid import RRF_K, rrf


def chunk(i, text):
    return SimpleNamespace(chunk_id=f"c{i}", doc_id="d", title="t", organization="o", section_path=[],
                           page_start=1, page_end=1, text=text, retrieval_text=text)


def test_analyzer_numbers_stopwords_stemming():
    assert analyze("The total trip cost exceeds $7,500") == ["total", "trip", "cost", "exceed", "7500"]
    assert analyze("$25,000.00 or less") == ["25000.00", "less"]
    assert analyze("4.4.3.1 the Data elements") == ["4.4.3.1", "data", "element"]
    assert analyze("PR7.1. Travel - Airfare") == ["pr7.1", "travel", "airfar"]
    assert analyze("Approvals are required.") == ["approv", "requir"]


def test_bm25_scores_match_formula():
    docs = [chunk(0, "travel card suspended"), chunk(1, "travel advance"), chunk(2, "lodging")]
    idx = BM25Index(docs, k1=0.9, b=0.4)
    n, df = 3, 1  # "suspend" occurs in one document
    idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
    dl, avg = 3, (3 + 2 + 1) / 3
    expected = idf * 1 * 1.9 / (1 + 0.9 * (1 - 0.4 + 0.4 * dl / avg))
    assert idx.scores("suspended")[0] == pytest.approx(expected)
    assert idx.scores("suspended")[1:] == [0.0, 0.0]


def test_bm25_ranking_is_deterministic_and_skips_zero_scores():
    docs = [chunk(i, "per diem meals") for i in range(3)] + [chunk(3, "unrelated text")]
    idx = BM25Index(docs)
    assert [i for i, _ in idx.ranked("per diem")] == [0, 1, 2]  # equal scores: corpus order
    assert [r.chunk_id for r in idx.search("per diem", k=2)] == ["c0", "c1"]
    assert idx.ranked("nothing matches") == []


def test_rrf_scores_and_ties():
    fused = rrf({"dense": [5, 1, 2], "bm25": [1, 5, 9]})
    s = {f.index: f.score for f in fused}
    assert s[5] == pytest.approx(1 / (RRF_K + 1) + 1 / (RRF_K + 2))
    assert s[9] == pytest.approx(1 / (RRF_K + 3))
    # 5 and 1 tie on score (ranks 1+2 each); both have best rank 1 -> corpus order puts 1 first
    assert [f.index for f in fused[:2]] == [1, 5]
    assert fused[0].ranks == {"dense": 2, "bm25": 1}
    assert [f.index for f in fused] == [f.index for f in rrf({"dense": [5, 1, 2], "bm25": [1, 5, 9]})]


def test_rrf_single_list_preserves_order():
    assert [f.index for f in rrf({"bm25": [4, 2, 7]})] == [4, 2, 7]
