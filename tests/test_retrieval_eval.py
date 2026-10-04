"""Retrieval evaluation: metric definitions, benchmark integrity, index search determinism."""

import json

import numpy as np
import pytest

from evaluation.retrieval.metrics import aggregate, case_metrics


def case(*units, qtype="direct_fact"):
    return {"question_type": qtype, "units": [{"sources": [{"chunk_id": c} for c in u]} for u in units]}


def test_single_unit_hit_and_mrr():
    c = case(["g1", "g2"])  # two acceptable sources for one fact
    m = case_metrics(c, ["x", "g2", "g1", "y"])
    assert (m["recall@1"], m["recall@3"], m["mrr@10"], m["first_gold_rank"]) == (0.0, 1.0, 0.5, 2)


def test_multi_unit_recall_is_fraction_of_units():
    c = case(["a"], ["b1", "b2"], qtype="cross_section")
    m = case_metrics(c, ["a", "x", "x", "x", "x", "b2"])
    assert m["recall@1"] == 0.5 and m["all_units@1"] == 0.0
    assert m["recall@5"] == 0.5 and m["recall@10"] == 1.0 and m["all_units@10"] == 1.0
    assert m["mrr@10"] == 1.0


def test_miss_beyond_top10_scores_zero():
    m = case_metrics(case(["g"]), [f"x{i}" for i in range(10)] + ["g"])
    assert m["recall@10"] == 0.0 and m["mrr@10"] == 0.0 and m["first_gold_rank"] is None


def test_aggregate_by_type():
    cases = [case(["a"]), case(["b"], qtype="numeric_threshold")]
    cases[1]["secondary_types"] = ["paraphrase"]
    per = [case_metrics(cases[0], ["a"]), case_metrics(cases[1], ["x", "b"])]
    agg = aggregate(per, cases)
    assert agg["overall"]["recall@1"] == 0.5 and agg["overall"]["mrr@10"] == 0.75
    assert agg["by_type"]["numeric_threshold"]["recall@3"] == 1.0
    assert agg["by_type"]["+paraphrase"]["n"] == 1


def test_index_search_is_exact_and_deterministic():
    from types import SimpleNamespace

    from retrieval.dense import DenseIndex
    from retrieval.embedders import CANDIDATES

    chunks = [SimpleNamespace(chunk_id=f"c{i}", doc_id="d", title="t", organization="o", section_path=[], page_start=1,
                              page_end=1, text=f"text {i}") for i in range(4)]
    m = np.array([[1, 0], [0, 1], [0.6, 0.8], [1, 0]], dtype=np.float32)  # c0 and c3 tie
    idx = DenseIndex(CANDIDATES["bge-small"], chunks, m, {})
    res = idx.search(np.array([1, 0], dtype=np.float32), k=3)
    assert [r.chunk_id for r in res] == ["c0", "c3", "c2"]  # ties broken by index order
    assert [r.rank for r in res] == [1, 2, 3] and res[2].score == pytest.approx(0.6)


def test_benchmark_matches_current_chunks():
    from chunking.config import CHUNKS_DIR
    from evaluation.retrieval.build import QUERIES, main as build_main

    if not (CHUNKS_DIR / "_manifest.json").exists():
        pytest.skip("chunks not generated")
    assert build_main(["--check"]) == 0
    lines = QUERIES.read_text(encoding="utf-8").splitlines()
    cases = [json.loads(line) for line in lines[1:]]
    assert 40 <= len(cases) <= 80
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert c["units"] and all(u["sources"] for u in c["units"])
        if c["question_type"] == "version_sensitive":
            docs = {s["doc_id"] for u in c["units"] for s in u["sources"]}
            assert len(docs) == 1 and c["expected_version"]  # exactly one UConn version is gold
