"""The integration suite is well-formed against the current chunks (offline, no LLM, no models).

The live run (python -m evaluation.integration.run) needs GROQ_API_KEY and is not part of pytest.
"""

import pytest

from evaluation.integration.cases import CASES, TAGS, resolve


@pytest.fixture(scope="module")
def chunks_by_doc():
    from chunking.config import CHUNKS_DIR
    from chunking.pipeline import load_chunks

    if not (CHUNKS_DIR / "_manifest.json").exists():
        pytest.skip("chunks not generated")
    return load_chunks()


def test_cases_cover_required_behaviors():
    tags = {t for c in CASES for t in c["tags"]}
    assert {"factual", "numeric", "conditional", "exception", "version_sensitive", "current", "version_neutral",
            "compare", "multi_chunk", "cross_section", "exact_citation", "unanswerable", "wrong_org",
            "evidence_but_insufficient", "prior_generation_failure", "prior_temporal_rerank_weakness"} <= tags
    assert all(set(c["tags"]) <= TAGS for c in CASES)
    assert len({c["id"] for c in CASES}) == len(CASES)
    assert {c["expect"] for c in CASES} == {"answer", "abstain"}


def test_case_fields_are_consistent():
    for c in CASES:
        assert c["intent"] in {"neutral", "point_in_time", "current", "compare"}
        if c["expect"] == "answer":
            assert c["orgs"] and c["gold"] and c["cite"], c["id"]
        else:
            assert c["orgs"] is None and not c["cite"] and not c["facts"], c["id"]


def test_every_reference_resolves_to_exactly_one_chunk(chunks_by_doc):
    for c in CASES:
        for r in [r for u in c["gold"] + c["cite"] for r in u] + c["forbidden"]:
            assert resolve(r, chunks_by_doc)
