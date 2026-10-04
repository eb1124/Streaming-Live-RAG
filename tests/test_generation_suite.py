"""The generation/evidence suite is well-formed against the current chunks (offline, no LLM).

The live run itself (python -m evaluation.generation.run) needs GROQ_API_KEY and is not part of pytest,
except when RUN_LIVE_LLM=1 is set explicitly.
"""

import os

import pytest

from evaluation.generation.cases import CASES, resolve


@pytest.fixture(scope="module")
def chunks_by_doc():
    from chunking.config import CHUNKS_DIR
    from chunking.pipeline import load_chunks

    if not (CHUNKS_DIR / "_manifest.json").exists():
        pytest.skip("chunks not generated")
    return load_chunks()


def test_cases_cover_required_categories():
    cats = {c["category"] for c in CASES}
    assert {"direct_fact", "numeric_threshold", "conditional", "exception", "citation_correctness", "multi_chunk",
            "insufficient_evidence", "conflicting_versions", "uconn_version", "end_to_end"} <= cats
    assert len({c["id"] for c in CASES}) == len(CASES)


def test_every_reference_resolves_to_exactly_one_chunk(chunks_by_doc):
    for case in CASES:
        refs = (case["context"] if case["context"] != "retrieve" else []) + case.get("must_cite", []) + case.get("must_not_cite", [])
        for r in refs:
            assert resolve(r, chunks_by_doc)
        if case["context"] != "retrieve":
            ctx = {resolve(r, chunks_by_doc) for r in case["context"]}
            assert {resolve(r, chunks_by_doc) for r in case.get("must_cite", [])} <= ctx  # answer is in the context


@pytest.mark.skipif(not (os.environ.get("RUN_LIVE_LLM") == "1" and os.environ.get("GROQ_API_KEY")),
                    reason="live LLM run disabled (set RUN_LIVE_LLM=1 and GROQ_API_KEY)")
def test_live_suite_passes():
    from evaluation.generation.run import main

    assert main([]) == 0
