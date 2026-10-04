"""Structure detection: headings, lists, sections, reading order."""

from ingestion.discovery import discover_pdfs
from ingestion.layout import xy_cut
from ingestion.pipeline import ingest_file
from ingestion.structure import match_marker, section_path
from tests.conftest import body, make_pdf


def _ingest(path):
    (f,) = [f for f in discover_pdfs(path.parent) if f.path == path]
    return ingest_file(f)


def _block(doc, needle):
    return next(b for b in doc.iter_blocks() if needle in b.text)


def test_headings_detected_from_size_and_weight(policy_pdf):
    doc = _ingest(policy_pdf)
    assert _block(doc, "Travel Policy").kind == "heading"
    assert _block(doc, "1. Purpose").kind == "heading"
    assert _block(doc, "Purchases over $5,000").kind == "paragraph"


def test_all_caps_body_text_is_not_a_heading(policy_pdf):
    doc = _ingest(policy_pdf)
    assert _block(doc, "THIS POLICY APPLIES TO ALL EMPLOYEES").kind != "heading"


def test_multiline_paragraph_is_one_block(policy_pdf):
    b = _block(_ingest(policy_pdf), "This policy governs")
    assert b.text == "This policy governs reimbursement of business travel expenses for all employees, students and guests of the university."
    assert b.line_count == 2


def test_bullet_and_enumerated_lists(policy_pdf):
    doc = _ingest(policy_pdf)
    bullets = [b for b in doc.pages[0].blocks if b.list_type == "bullet"]
    assert [b.marker for b in bullets] == ["-", "-", "-"]
    a = _block(doc, "(a) first enumerated")
    assert a.kind == "list_item" and a.list_type == "ordered" and a.number == "a"


def test_heading_levels_and_section_paths(policy_pdf):
    doc = _ingest(policy_pdf)
    top = _block(doc, "Travel Policy")
    sub = _block(doc, "2. Approvals")
    assert top.heading_level < sub.heading_level
    rule = _block(doc, "Purchases over $5,000")
    assert section_path(doc, rule.section_id) == ["Travel Policy", "2. Approvals"]
    sec = next(s for s in doc.sections if s.title == "2. Approvals")
    assert sec.number == "2" and sec.page_start == 1


def test_labeled_metadata_is_extracted(policy_pdf):
    md = _ingest(policy_pdf).metadata
    assert md.effective_date.value == "May 1, 2026"
    assert md.effective_date.normalized == "2026-05-01"
    assert md.effective_date.source == "document_text"
    assert md.title.value == "Travel Policy"


def test_match_marker():
    assert match_marker("• Receipts are required") == ("bullet", "•", None)
    assert match_marker("3.2.1. Contracts between") == ("ordered", "3.2.1.", "3.2.1")
    assert match_marker("4.4.3.1 the Data elements") == ("ordered", "4.4.3.1", "4.4.3.1")
    assert match_marker("(1) Catalog methods") == ("ordered", "(1)", "1")
    assert match_marker("b. Purchasing Methods") == ("ordered", "b.", "b")
    assert match_marker("ii. Sourcing: When") == ("ordered", "ii.", "ii")
    assert match_marker("PR1.1. Third Party Prepayments") == ("ordered", "PR1.1.", "PR1.1")
    for not_a_list in ["2025 was a year", "$5,000 limit", "September, 1990", "Note: this"]:
        assert match_marker(not_a_list) is None, not_a_list


def test_xy_cut_reads_grid_columns_before_rows():
    # A 2-column metadata grid with tight rows: read each column top to bottom.
    items = [
        ("EFFECTIVE", (50, 100, 120, 110)), ("REVIEWED", (300, 100, 380, 110)),
        ("September,", (50, 113, 120, 123)), ("May, 2026", (300, 113, 380, 123)),
        ("1990", (50, 126, 90, 136)),
    ]
    order = [t for t, _ in xy_cut(items, lambda i: i[1], strong_gap=12)]
    assert order == ["EFFECTIVE", "September,", "1990", "REVIEWED", "May, 2026"]


def test_xy_cut_keeps_gutter_numbers_with_their_paragraph():
    # "1" sits in a left gutter beside paragraph 1; paragraphs are separated by a large gap.
    items = [
        ("para1-line1", (200, 100, 500, 112)), ("1", (165, 108, 172, 120)), ("para1-line2", (200, 121, 500, 133)),
        ("para2-line1", (200, 160, 500, 172)), ("2", (165, 168, 172, 180)), ("para2-line2", (200, 181, 500, 193)),
    ]
    order = [t for t, _ in xy_cut(items, lambda i: i[1], strong_gap=13)]
    assert order == ["1", "para1-line1", "para1-line2", "2", "para2-line1", "para2-line2"]


def test_policy_section_numbers_are_enumerators():
    assert match_marker("5002.1 Remote-Designated Functions") == ("ordered", "5002.1", "5002.1")


# --- line joining: repairs found in the corpus quality pass ---------------------------------

from ingestion.models import Block, Line  # noqa: E402
from ingestion.structure import DocStyle, _flag_breadcrumbs, _should_break  # noqa: E402

STYLE = DocStyle(body_size=11, body_bold=False, line_width=460, para_gap=8)


def _line(text, x0, y0, x1, size=11.0, bold=0.0, font="Tahoma"):
    return Line(line_id="l", page=1, bbox=(x0, y0, x1, y0 + size + 1), raw_text=text, text=text, font=font, size=size, bold_ratio=bold)


def test_wrapped_line_continues_despite_indent():
    # Rutgers p1: a bold note wraps; the second line starts further right.
    a = _line("This procedure is in accordance with University Policy 40.4.1: Travel and Business", 107, 178, 525, 10, 1.0)
    b = _line("Expense Policy.", 136, 191, 221, 10, 1.0)
    assert not _should_break(a, b, [a], STYLE)
    short = _line("Travel and Business", 107, 178, 200, 10, 1.0)  # a short line is not a wrap
    assert _should_break(short, b, [short], STYLE)


def test_open_parenthesis_continues_into_next_line():
    # UT p16: bold definition term broken inside a parenthesis.
    a = _line("University of Texas System Administration (U. T. System", 105, 264, 427, 12, 1.0)
    b = _line("Administration) - the central administrative offices that provide oversight", 105, 282, 466, 12, 0.1)
    assert not _should_break(a, b, [a], STYLE)


def test_bare_clause_number_takes_the_text_below_it():
    # McGill p10: "PR6.1." on its own line under the PR6 heading.
    head = _line("PR6. Receipts/Supporting Documentation", 72, 74, 320, 14, 1.0, "Calibri-Bold")
    num = _line("PR6.1.", 90, 91, 129, 11.9, 1.0, "Calibri-Bold")
    body_ = _line("Receipts/supporting documentation are required for all expenses", 90, 106, 534, 12, 0.0, "Calibri")
    assert _should_break(head, num, [head], STYLE)
    assert not _should_break(num, body_, [num], STYLE)


def test_reference_at_end_of_wrapped_sentence_is_not_a_clause_number():
    # McGill p5: "... Please refer to the Travel and Other Expenses Policy" / "P3.6."
    a = _line("Expense Report. Please refer to the Travel and Other Expenses Policy", 108, 194, 540, 12)
    ref = _line("P3.6.", 108, 209, 140, 12)
    assert not _should_break(a, ref, [a], STYLE)


def test_breadcrumb_row_is_flagged():
    def blk(text, x, y=247):
        return Block(block_id=f"b{x}", page=1, kind="paragraph", text=text, bbox=(x, y, x + 20, y + 11), line_ids=[], size=9)
    row = [blk("Home", 50), blk("ISO Policies, Standards, and Guidelines", 86), blk("INFORMATION RESOURCES POLICY", 230)]
    other = blk("Home is where the policy applies to remote work.", 50, 400)
    _flag_breadcrumbs(row + [other])
    assert all("suspected_chrome:breadcrumb" in b.flags for b in row)
    assert not other.flags
