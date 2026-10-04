"""Checks against the real corpus: facts verified by hand in the source PDFs.

Skipped automatically when corpus/ has no PDFs. Run just these with:  pytest -m corpus
"""

import re
import unicodedata

import pymupdf
import pytest

from ingestion.config import OVERRIDES_FILE
from ingestion.discovery import discover_pdfs, file_sha256
from ingestion.pipeline import ingest_file
from ingestion.profiles import load_overrides
from ingestion.structure import is_retrievable, section_path

pytestmark = pytest.mark.corpus


@pytest.fixture(scope="module")
def docs():
    from ingestion.config import CORPUS_DIR

    files = discover_pdfs(CORPUS_DIR) if CORPUS_DIR.exists() else []
    if not files:
        pytest.skip("real corpus not available")
    overrides = load_overrides(OVERRIDES_FILE)
    before = {f.relative_path: file_sha256(f.path) for f in files}
    result = {f.original_filename: ingest_file(f, overrides.get(f.original_filename)) for f in files}
    assert before == {f.relative_path: file_sha256(f.path) for f in files}, "corpus modified"
    return result


def _doc(docs, fragment):
    (d,) = [d for name, d in docs.items() if fragment in name]
    return d


def _text(doc, page=None):
    return "\n".join(b.text for b in doc.iter_blocks() if page is None or b.page == page)


def _norm(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s)).strip()


def test_all_documents_ingested_with_correct_page_counts(docs):
    assert len(docs) == 12
    for name, d in docs.items():
        assert d.status == "ok", name
        from ingestion.config import CORPUS_DIR

        with pymupdf.open(CORPUS_DIR / d.source.relative_path) as src:
            assert d.pdf.page_count == src.page_count == len(d.pages)


def test_every_overridden_file_exists(docs):
    assert set(load_overrides(OVERRIDES_FILE)) <= set(docs)


def test_no_override_was_rejected(docs):
    for name, d in docs.items():
        assert not [w for w in d.warnings if w.code == "override_rejected"], name


def test_every_document_has_identity(docs):
    for name, d in docs.items():
        assert d.metadata.title.value, name
        assert d.metadata.organization.value, name
        assert d.metadata.domain.value in {"travel_expenses", "procurement", "remote_work", "information_security"}
        assert d.metadata.document_type.value in {"policy", "procedure", "standard", "guidance"}, name
        assert d.metadata.authority_level.value in {"authoritative", "official_web", "informational"}, name
        assert d.metadata.source_url.value or d.profile == "office_export", name


def test_kept_lines_map_to_source_page_text(docs):
    """Every kept line's raw text occurs in PyMuPDF's text of the same page."""
    from ingestion.config import CORPUS_DIR

    for name, d in docs.items():
        with pymupdf.open(CORPUS_DIR / d.source.relative_path) as src:
            misses = total = 0
            for page in d.pages:
                page_text = src[page.page_number - 1].get_text("text")
                source = _norm(page_text).replace(" ", "")
                page_words = set(re.findall(r"\w+", _norm(page_text)))
                for ln in page.lines:
                    if ln.removed:
                        continue
                    total += 1
                    # Substring match; for lines merged from interleaved re-renders (text-shadow),
                    # fall back to "every word of the line occurs on this page".
                    if _norm(ln.raw_text).replace(" ", "") not in source and not set(
                        re.findall(r"\w+", _norm(ln.raw_text))
                    ) <= page_words:
                        misses += 1
            assert misses <= 0.01 * total, f"{name}: {misses}/{total} lines not found on their page"


def test_browser_chrome_removed_but_recoverable(docs):
    d = _doc(docs, "2305 Compliance")
    assert "utm_source" not in _text(d)
    assert "https://finance.upenn.edu" not in _text(d)
    assert "https://finance.upenn.edu/policy/2305-compliance-with-procurement-policies/" in d.pages[0].raw_text
    assert d.metadata.source_url.value == "https://finance.upenn.edu/policy/2305-compliance-with-procurement-policies/"
    assert d.metadata.captured_at.normalized == "2026-09-24T23:39"
    assert d.metadata.title.value == "2305 Compliance with Procurement Policies"


def test_word_ligature_damage_repaired(docs):
    d = _doc(docs, "Travel-and-Entertainment-Procedures-FINAL")
    text = _text(d)
    assert "travelers are permitted to book airfare" in text
    assert "https://travel.uconn.edu/" in text
    assert "Ɵ" not in text and "aŌer" not in text
    assert "after 90 days" in text


def test_policy_numbers_and_conditions_survive(docs):
    stanford = _doc(docs, "5.1.1 Procurement")
    assert "The total difference does not exceed $250." in _text(stanford, page=3)
    penn = _doc(docs, "2305 Compliance")
    assert "Final approval authority for University POs less than $50,000" in _text(penn, page=2)
    ut = _doc(docs, "INFORMATION RESOURCES")
    assert "4.4.3.1 the Data elements to be captured in logs;" in _text(ut, page=40)
    oregon = _doc(docs, "03-010 Procurement Thresholds")
    assert "interest will commence accruing 30 days after the contractor" in _text(oregon, page=9)


def test_labeled_dates_and_versions(docs):
    yale = _doc(docs, "5002 Remote Work")
    assert yale.metadata.effective_date.value == "August 31, 2022"
    assert yale.metadata.revision_date.normalized == "2022-08-31"
    mcgill = _doc(docs, "procedures_for_travel")
    assert mcgill.metadata.version.value == "V6.9"
    assert mcgill.metadata.effective_date.normalized == "2026-05-01"
    oregon = _doc(docs, "03-010 Procurement Thresholds")
    assert oregon.metadata.revision_date.normalized == "2026-07-15"
    uconn_policy = _doc(docs, "Travel and Entertainment Policy")
    assert uconn_policy.metadata.effective_date.normalized == "2026-07-01"
    assert uconn_policy.metadata.captured_at.normalized == "2026-09-25T01:12"


def test_tables_detected(docs):
    mcgill = _doc(docs, "procedures_for_travel")
    meal = next(b for b in mcgill.iter_blocks() if b.kind == "table" and "Within Canada" in b.text)
    assert meal.page == 17
    assert meal.table.rows[1] == ["Breakfast", "$24 CAD*", "$30 CAD*"]
    uconn = _doc(docs, "Travel-and-Entertainment-Procedures-FINAL")
    per_diem = next(b for b in uconn.iter_blocks() if b.kind == "table" and "Breakfast" in b.text)
    assert per_diem.table.rows[0][:4] == ["Breakfast", "Lunch", "Dinner", "Total"]
    assert per_diem.table.rows[1][0] == "$ 11.00"


def test_section_structure(docs):
    oregon = _doc(docs, "03-010 Procurement Thresholds")
    assert "5. Responsibilities & Procedures" in [s.title for s in oregon.sections]
    clause = next(b for b in oregon.iter_blocks() if b.number == "5.1.1")
    assert clause.text.endswith("based on the anticipated contract price:")  # hanging continuation kept
    assert section_path(oregon, clause.section_id)[-1] == "5. Responsibilities & Procedures"
    yale = _doc(docs, "5002 Remote Work")
    titles = [s.title for s in yale.sections]
    assert "5002.5 Implementation of Remote Work Arrangements" in titles
    rutgers = _doc(docs, "Chapter 11")
    assert any(s.title.startswith("11.4.9 Car Rental Reservations") and s.page_start == 10 for s in rutgers.sections)


def test_yale_replacement_contains_policy_text(docs):
    yale = _doc(docs, "5002 Remote Work")
    text = _text(yale)
    assert "Work units and Covered Staff Members must implement Remote Work arrangements" in text
    assert not [w for w in yale.warnings if w.code == "empty_after_cleaning"]
    assert sum(1 for p in yale.pages for ln in p.lines if ln.removed == "duplicate_render") > 40
    assert yale.metadata.source_url.source == "curated" and yale.metadata.captured_at.value == "2026-09-25"


def test_superseded_files_are_not_in_corpus(docs):
    names = set(docs)
    assert "5002 Remote Work Policy _ It’s Your Yale.pdf" not in names
    assert "Travel Policy _ Travel Services _ Procurement _ University of Connecticut.pdf" not in names
    assert not any(n.startswith("Procurement Thresholds and Methods _ ") for n in names)


def test_curated_classifications(docs):
    mich = _doc(docs, "Travel Booking")
    assert (mich.metadata.document_type.value, mich.metadata.authority_level.value) == ("guidance", "informational")
    assert mich.metadata.captured_at.normalized  # time-sensitive content keeps its capture date
    assert any(w.code == "curator_flag" for w in mich.warnings)
    rutgers = _doc(docs, "Chapter 11")
    assert rutgers.metadata.title.source == "document_text"  # not the filename / placeholder PDF title
    assert rutgers.metadata.domain.value == "travel_expenses"
    mcgill = _doc(docs, "procedures_for_travel")
    assert "INDIRECT" in mcgill.metadata.organization.note
    assert mcgill.metadata.organization.evidence == "McGill students"
    assert "McGill University" not in _text(mcgill)  # the full name is our normalization, not a quote


def test_ut_revision_history_excluded_but_preserved(docs):
    ut = _doc(docs, "INFORMATION RESOURCES")
    rev = [b for b in ut.iter_blocks() if "Revision History" in section_path(ut, b.section_id)]
    assert len(rev) > 50
    assert all(not is_retrievable(b) and b.confidence == "low" for b in rev)
    assert any("11/18/2023" in b.text for b in rev)  # original extraction still there
    assert all(is_retrievable(b) for b in ut.iter_blocks() if b.page == 40)


def test_uconn_procedures_versions_kept_separately(docs):
    feb = _doc(docs, "Travel-and-Entertainment-Procedures-FINAL")
    jul = _doc(docs, "2026-07-01-Travel-and-Entertainment-Procedures")
    assert feb.doc_id != jul.doc_id
    assert feb.source.file_sha256 != jul.source.file_sha256
    assert feb.metadata.title.value == jul.metadata.title.value == "Travel and Entertainment Procedures"
    assert feb.metadata.series_id.value == jul.metadata.series_id.value == "uconn-travel-entertainment-procedures"
    assert feb.metadata.effective_date.normalized == "2026-02-01"
    assert jul.metadata.effective_date.normalized == "2026-07-01"
    assert feb.metadata.superseded_date.normalized == "2026-07-01"
    assert jul.metadata.superseded_date.value is None
    assert (feb.metadata.is_current.value, jul.metadata.is_current.value) == ("false", "true")
    assert jul.metadata.revision_date.normalized == "2026-06-17"
    assert jul.metadata.version.value is None and feb.metadata.version.value is None  # neither states one


def test_print_to_pdf_calibri_repaired(docs):
    jul = _doc(docs, "2026-07-01-Travel-and-Entertainment-Procedures")
    text = _text(jul)
    assert "travelers are permitted to book airfare" in text
    assert "Executive Vice President" in text
    assert not re.search("[ƟŌƞƩ]", text)
