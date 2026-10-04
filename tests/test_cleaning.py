"""Cleaning removes boilerplate, keeps policy content, and stays reversible."""

from ingestion.discovery import discover_pdfs
from ingestion.normalize import normalize_text, repair_calibri
from ingestion.pipeline import ingest_file
from tests.conftest import body, make_pdf


def _ingest(path):
    (f,) = [f for f in discover_pdfs(path.parent) if f.path == path]
    return ingest_file(f)


def _clean_text(doc):
    return "\n".join(b.text for b in doc.iter_blocks())


def test_running_header_footer_and_page_numbers_removed(policy_pdf):
    doc = _ingest(policy_pdf)
    text = _clean_text(doc)
    assert "ACME University Travel Procedures" not in text
    assert "Confidential - internal use only" not in text
    assert "Page 2 of 4" not in text
    reasons = {ln.removed for p in doc.pages for ln in p.lines if ln.removed}
    assert reasons <= {"repeated_boilerplate", "page_number"}


def test_removal_is_reversible(policy_pdf):
    doc = _ingest(policy_pdf)
    for page in doc.pages:
        removed = [ln for ln in page.lines if ln.removed]
        assert any(ln.text == "ACME University Travel Procedures" for ln in removed)
        assert "ACME University Travel Procedures" in page.raw_text  # original text untouched


def test_important_content_is_not_removed(policy_pdf):
    text = _clean_text(_ingest(policy_pdf))
    for phrase in [
        "Purchases over $5,000 require approval by the Vice President.",
        "Receipts are required for expenses of $75 or more.",
        "Alcohol is not reimbursable.",
        "Note: Occasional ad hoc requests are not covered by this policy.",
        "Effective Date: May 1, 2026",
        # repeated on 3 of 4 pages at the same position, but it is body text of normal size
        "Exceptions require written approval.",
    ]:
        assert phrase in text, phrase


def test_repeated_sentence_at_same_position_is_only_removed_when_on_most_pages(tmp_path):
    pages = [[body(100, "Receipts must be retained for seven years.")] if i < 2 else [body(100, f"Page body {i}.")] for i in range(6)]
    doc = _ingest(make_pdf(tmp_path / "c" / "r.pdf", pages))
    assert _clean_text(doc).count("Receipts must be retained for seven years.") == 2


def test_short_repeated_tokens_are_kept(tmp_path):
    # e.g. "Yes"/"No" cells or bullets at the same place on every page must not vanish
    pages = [[body(100, "Yes"), body(130, f"Question {i} text.")] for i in range(4)]
    doc = _ingest(make_pdf(tmp_path / "c" / "yn.pdf", pages))
    assert _clean_text(doc).count("Yes") == 4


def test_normalization_repairs_ligatures_and_spaces_without_dropping_letters():
    text, repairs = normalize_text("eﬃcient  reﬁnance  ")
    assert text == "efficient refinance"
    assert repairs  # recorded


def test_calibri_repair_mapping():
    assert repair_calibri("ExecuƟve aŌer Harƞord") == ("Executive after Hartford", 3)
    assert repair_calibri("permiƩed") == ("permitted", 1)


def test_numbers_and_symbols_survive_normalization():
    s = "Amounts ≥ $5,000.00 (10%) — see §4.2.1; per diem 75%."
    assert normalize_text(s)[0] == s


def test_page_numbers_need_a_consistent_sequence(tmp_path):
    # Bottom-margin "1".."3" are page numbers; a lone "4" in the top margin of page 2 is a clause number.
    pages = [
        [body(200, "First page policy text."), body(740, "1", x=300)],
        [(165, 45, "4", 10.5, "hebo"), body(45, "All approved suppliers are listed in the marketplace.", x=200), body(740, "2", x=300)],
        [body(200, "Third page policy text."), body(740, "3", x=300)],
    ]
    doc = _ingest(make_pdf(tmp_path / "c" / "n.pdf", pages))
    removed = [ln.text for p in doc.pages for ln in p.lines if ln.removed == "page_number"]
    assert removed == ["1", "2", "3"]
    clause = next(b for b in doc.pages[1].blocks if "approved suppliers" in b.text)
    assert clause.text.startswith("4 ") and clause.number == "4" and "gutter_number_attached" in clause.flags
