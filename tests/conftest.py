"""Test helpers: build small synthetic PDFs with PyMuPDF so tests do not depend on corpus/."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from ingestion.config import CORPUS_DIR

# (x, baseline_y, text, fontsize, fontname) ; fonts: "helv" regular, "hebo" bold
Item = tuple[float, float, str, float, str]


def make_pdf(path: Path, pages: list[list[Item]], title: str | None = None) -> Path:
    doc = pymupdf.open()
    for items in pages:
        page = doc.new_page(width=612, height=792)
        for x, y, text, size, font in items:
            page.insert_text((x, y), text, fontsize=size, fontname=font)
    if title:
        doc.set_metadata({"title": title})
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def body(y: float, text: str, x: float = 72) -> Item:
    return (x, y, text, 11, "helv")


def heading(y: float, text: str, size: float = 16) -> Item:
    return (72, y, text, size, "hebo")


def running_header_footer(page_no: int, n_pages: int) -> list[Item]:
    return [
        (72, 40, "ACME University Travel Procedures", 9, "helv"),
        (72, 760, "Confidential - internal use only", 9, "helv"),
        (500, 760, f"Page {page_no} of {n_pages}", 9, "helv"),
    ]


@pytest.fixture
def policy_pdf(tmp_path) -> Path:
    """A 4-page policy with running header/footer, headings, lists and key numbers."""
    n = 4
    pages = []
    p1 = running_header_footer(1, n) + [
        heading(100, "Travel Policy", 20),
        body(130, "Effective Date: May 1, 2026"),
        heading(170, "1. Purpose", 14),
        body(195, "This policy governs reimbursement of business travel expenses for all"),
        body(209, "employees, students and guests of the university."),
        heading(245, "2. Approvals", 14),
        body(270, "Purchases over $5,000 require approval by the Vice President."),
        body(300, "- Airfare must be booked in economy class."),
        body(318, "- Receipts are required for expenses of $75 or more."),
        body(336, "- Alcohol is not reimbursable."),
        body(370, "Note: Occasional ad hoc requests are not covered by this policy."),
    ]
    pages.append(p1)
    for i in range(2, n + 1):
        items = running_header_footer(i, n) + [
            heading(100, f"{i + 1}. Section {i + 1}", 14),
            body(125, f"Content that only appears on page {i}. Exceptions require written approval."),
            body(150, "THIS POLICY APPLIES TO ALL EMPLOYEES WITHOUT EXCEPTION"),
            body(175, "(a) first enumerated condition applies here"),
            body(193, "(b) second enumerated condition applies here"),
        ]
        pages.append(items)
    return make_pdf(tmp_path / "corpus" / "policy.pdf", pages, title="Travel Policy")


@pytest.fixture
def real_corpus():
    pdfs = list(CORPUS_DIR.glob("**/*.pdf")) if CORPUS_DIR.exists() else []
    if not pdfs:
        pytest.skip("real corpus not available")
    return CORPUS_DIR
