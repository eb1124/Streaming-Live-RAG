"""Chunking the real corpus: facts checked by hand against the source PDFs.

Ingests corpus/ in memory (data/ingested/ is not read or written). Run with:  pytest -m corpus
"""

import pytest

from chunking import config as C
from chunking.chunker import chunk_document
from ingestion.config import CORPUS_DIR, OVERRIDES_FILE
from ingestion.discovery import discover_pdfs
from ingestion.pipeline import ingest_file
from ingestion.profiles import load_overrides

pytestmark = pytest.mark.corpus


@pytest.fixture(scope="module")
def corpus():
    files = discover_pdfs(CORPUS_DIR) if CORPUS_DIR.exists() else []
    if not files:
        pytest.skip("real corpus not available")
    overrides = load_overrides(OVERRIDES_FILE)
    docs = {f.original_filename: ingest_file(f, overrides.get(f.original_filename)) for f in files}
    return {name: (doc, chunk_document(doc)) for name, doc in docs.items()}


def _get(corpus, fragment):
    (hit,) = [v for name, v in corpus.items() if fragment in name]
    return hit


def test_every_document_is_chunked_within_limits(corpus):
    ids = []
    for name, (doc, res) in corpus.items():
        assert res.chunks, name
        for c in res.chunks:
            assert c.retrieval_token_count <= C.MAX_TOKENS, c.chunk_id
            assert c.text.strip()
        ids += [c.chunk_id for c in res.chunks + res.excluded]
        bad = [d for d in res.diagnostics if d["code"] in ("block_not_chunked", "duplicate_chunk_id", "oversized_chunk")]
        assert not bad, (name, bad)
    assert len(ids) == len(set(ids))


def test_chunk_ids_stable_across_runs(corpus):
    doc, res = _get(corpus, "03-010 Procurement")
    again = chunk_document(doc)
    assert [c.chunk_id for c in again.chunks] == [c.chunk_id for c in res.chunks]
    assert [c.content_hash for c in again.chunks] == [c.content_hash for c in res.chunks]


def test_uconn_procedure_versions_are_separate_documents(corpus):
    feb_doc, feb = _get(corpus, "Travel-and-Entertainment-Procedures-FINAL")
    jul_doc, jul = _get(corpus, "2026-07-01-Travel-and-Entertainment-Procedures")
    assert feb_doc.doc_id != jul_doc.doc_id
    assert not {c.chunk_id for c in feb.chunks} & {c.chunk_id for c in jul.chunks}
    assert all(c.doc_id == feb_doc.doc_id for c in feb.chunks) and all(c.doc_id == jul_doc.doc_id for c in jul.chunks)
    f, j = feb.chunks[0], jul.chunks[0]
    assert (f.effective_date, f.superseded_date, f.is_current) == ("2026-02-01", "2026-07-01", False)
    assert (j.effective_date, j.superseded_date, j.is_current) == ("2026-07-01", None, True)
    assert f.series_id == j.series_id == "uconn-travel-entertainment-procedures"
    assert f.title == j.title and f.organization == j.organization == "University of Connecticut"
    assert f.version is None and j.version is None  # neither PDF states a version
    assert any("Approval Date: November 19, 2025" in c.text for c in feb.chunks)
    assert any("Approval Date: June 17, 2026" in c.text for c in jul.chunks)
    assert not any("June 17, 2026" in c.text for c in feb.chunks)


def test_oregon_clause_hierarchy(corpus):
    _, res = _get(corpus, "03-010 Procurement")
    by_clause = {c.clause_id: c for c in res.chunks if c.clause_id}
    c511 = next(c for c in res.chunks if "5.1.1" in c.clause_ids)
    assert c511.section_path == ["Procurement Thresholds and Methods", "5. Responsibilities & Procedures",
                                 "5.1. General Procurement Threshold"]
    assert "a. $25,000.00 or less" in c511.text and "c. Greater than $250,000.00" in c511.text  # sub-items stay with 5.1.1
    assert c511.text.startswith("5.1.1. When purchasing")  # the title-only clause 5.1 is in the path, not the text
    assert by_clause["5.7"].section_path[-2:] == ["5. Responsibilities & Procedures", "5.7. Conflict of Interest"]
    frags = [c for c in res.chunks if c.clause_id == "5.2.1"]
    assert len(frags) == 3
    assert frags[0].text.startswith("5.2.1.") and frags[0].lead_in is None
    assert all(f.lead_in.startswith("5.2.1. When acquiring goods") for f in frags[1:])
    assert frags[1].text.startswith("c. Formal procurement") and frags[2].text.startswith("f. Noncompetitive")
    assert "i. Acquiring goods, services, or construction from a sole source" in frags[2].text  # roman items stay with f.
    ids = [c for c in res.chunks if "5.17.4" in c.clause_ids]
    assert len(ids) == 1 and ids[0].clause_id == "5.17.4"


def test_penn_numbered_clauses(corpus):
    _, res = _get(corpus, "2305 Compliance")
    clauses = [i for c in res.chunks for i in c.clause_ids]
    assert clauses == ["1", "2", "3", "4", "5", "6", "7"]
    c5 = next(c for c in res.chunks if "5" in c.clause_ids)
    assert "equal to or greater than $50,000 are subject to Procurement" in c5.text  # unnumbered continuation of 5
    c7 = next(c for c in res.chunks if c.clause_id == "7")
    assert c7.page_start == 2 and c7.page_end == 3  # clause 7 continues on page 3
    assert not any("Give Us Feedback" in c.text or "Penn A-Z" in c.text for c in res.chunks)


def test_ut_clauses_long_sections_and_exclusions(corpus):
    doc, res = _get(corpus, "INFORMATION RESOURCES")
    c44 = next(c for c in res.chunks if c.clause_id == "4.4")
    assert {"4.4.3", "4.4.3.1", "4.4.3.2", "4.4.3.3"} <= set(c44.clause_ids)
    assert "4.4.3.1 the Data elements to be captured in logs;" in c44.text
    defs = [c for c in res.chunks if c.section_path[-1:] == ["Definitions"]]
    assert len(defs) > 10 and all(c.split == "paragraph" for c in defs)  # long section, cut between definitions
    c91 = next(c for c in res.chunks if "9.1" in c.clause_ids)
    assert c91.section_path[1] == "UT-IRUSP Standard 9: Data Classification."  # heading recovered
    c171 = next(c for c in res.chunks if "17.1" in c.clause_ids)
    assert c171.section_path[1] == "UT-IRUSP Standard 17: Security Monitoring."
    grid = [c for c in res.chunks if "unreconstructed_table_region" in c.confidence_reasons]
    assert grid and all(c.confidence == "low" and 47 <= c.page_start <= 48 for c in grid)
    # Revision history starts part-way down p78 (Standard 24 ends above it) and runs to p93.
    assert not any("Revision History" in c.section_path or c.page_end > 78 for c in res.chunks)
    rev = [c for c in res.excluded if c.exclusion_reason == "revision_history_table_unreliable"]
    rev_pages = {p for c in rev for p in c.pages}
    assert min(rev_pages) == 78 and set(range(78, 94)) <= rev_pages
    assert any("11/18/2023" in c.text for c in rev)
    toc = [c for c in res.excluded if c.exclusion_reason == "navigation_toc"]
    assert any("Policy Statement" in c.text for c in toc)
    assert any(c.section_path[-1:] == ["Policy Statement"] for c in res.chunks)  # the section itself is kept


def test_mcgill_numbered_lists_stay_inside_their_clause(corpus):
    _, res = _get(corpus, "procedures_for_travel")
    airfare = [c for c in res.chunks if c.clause_id == "PR7.1"]
    assert airfare and all(c.section_path[-1].startswith("PR7.1.") for c in airfare)
    assert all(set(c.clause_ids) == {"PR7.1"} for c in airfare)  # "1." ... "10." are list items, not clauses
    per_diem = next(c for c in res.chunks if "| Breakfast | $24 CAD* | $30 CAD* |" in c.text)
    assert per_diem.clause_id == "PR7.7" and per_diem.tables[0].confidence == "medium"


def test_no_duplicate_chunks(corpus):
    for name, (doc, res) in corpus.items():
        hashes = [c.content_hash for c in res.chunks]
        assert len(hashes) == len(set(hashes)), name
        seen = {}
        for c in res.chunks:
            for bid in c.source_block_ids:
                seen.setdefault(bid, []).append(c.char_span)
        assert all(len(v) == 1 or all(s is not None for s in v) for v in seen.values()), name


def test_upstream_quality_pass_repairs(corpus):
    # 1. Rutgers: the wrapped bold note is one sentence, not a heading plus "Expense Policy."
    _, rutgers = _get(corpus, "Chapter 11")
    assert rutgers.chunks[0].text == "This procedure is in accordance with University Policy 40.4.1: Travel and Business Expense Policy."
    assert not any(c.text == "Expense Policy." for c in rutgers.chunks)
    # 2. UT: the definition term broken inside a parenthesis stays with its definition.
    _, ut = _get(corpus, "INFORMATION RESOURCES")
    assert any("University of Texas System Administration (U. T. System Administration) - the central" in c.text for c in ut.chunks)
    assert not any(c.text.startswith("Administration)") for c in ut.chunks)
    # 3. McGill: PR6 is a heading; PR6.1 / PR6.2 are clauses carrying their own text.
    _, mcgill = _get(corpus, "procedures_for_travel")
    pr6 = next(c for c in mcgill.chunks if "PR6.1" in c.clause_ids)
    assert pr6.section_path[-1] == "PR6. Receipts/Supporting Documentation"
    assert pr6.text.startswith("PR6.1. Receipts/supporting documentation are required")
    assert "PR6.2" in pr6.clause_ids
    # 4/5. Stanford: other memos' numbers are not clause ids; the source's own truncated teaser is kept as is.
    _, stanford = _get(corpus, "5.1.1 Procurement")
    ids = {i for c in stanford.chunks for i in c.clause_ids + [c.clause_id]}
    assert not ids & {"5.1.1", "3.2.1", "1.5.2", "5.3.3"}
    assert any(c.text.startswith("his policy applies") for c in stanford.chunks)  # missing "T" is in the source page
    # 6. Navigation / service clutter is excluded, not deleted.
    ut_x = {c.exclusion_reason for c in ut.excluded}
    assert "site_chrome:breadcrumb" in ut_x
    assert not any(c.text.startswith("Home\n") or c.text == "Revision History" for c in ut.chunks)
    _, mich = _get(corpus, "Travel Booking")
    assert not any("Slack" in c.text or "Newsletter" in c.text for c in mich.chunks)
    assert any(c.exclusion_reason == "navigation_links" and "Slack" in c.text for c in mich.excluded)
    assert any("Phone: (877) 804-3688" in c.text for c in mich.chunks)  # service contacts are content, kept
    # 7. Rochester: the date stamp is metadata, not a chunk.
    roch_doc, roch = _get(corpus, "International Travel Policy")
    assert roch_doc.metadata.issued_date.normalized == "2026-03"
    assert all(c.issued_date == "2026-03" for c in roch.chunks)
    assert not any("ISSUED ON" in c.text for c in roch.chunks)
    assert any(c.exclusion_reason == "document_metadata" for c in roch.excluded)
