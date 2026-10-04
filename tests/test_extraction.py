"""Extraction: page numbering, empty/failed pages, source mapping, failure handling."""

import pymupdf

from ingestion.discovery import discover_pdfs
from ingestion.pipeline import ingest_file
from tests.conftest import body, make_pdf


def _ingest(path):
    (f,) = [f for f in discover_pdfs(path.parent) if f.path == path]
    return ingest_file(f)


def test_pages_are_numbered_from_one_in_pdf_order(policy_pdf):
    doc = _ingest(policy_pdf)
    assert doc.status == "ok"
    assert doc.pdf.page_count == 4
    assert [p.page_number for p in doc.pages] == [1, 2, 3, 4]
    for page in doc.pages:
        assert all(ln.page == page.page_number for ln in page.lines)
        assert all(ln.line_id.startswith(f"p{page.page_number}-") for ln in page.lines)
        assert all(b.page == page.page_number for b in page.blocks)


def test_text_is_extracted_per_page(policy_pdf):
    doc = _ingest(policy_pdf)
    for n in (2, 3, 4):
        text = " ".join(b.text for b in doc.pages[n - 1].blocks)
        assert f"Content that only appears on page {n}." in text
        other = [p for p in doc.pages if p.page_number != n]
        assert all(f"only appears on page {n}." not in " ".join(b.text for b in p.blocks) for p in other)


def test_blocks_map_back_to_source_lines_and_positions(policy_pdf):
    doc = _ingest(policy_pdf)
    src = pymupdf.open(policy_pdf)
    for page in doc.pages:
        lines = {ln.line_id: ln for ln in page.lines}
        for block in page.blocks:
            assert block.line_ids, block
            for lid in block.line_ids:
                ln = lines[lid]  # every referenced line exists on the same page
                assert ln.removed is None
                x0, y0, x1, y1 = block.bbox
                assert x0 - 0.5 <= ln.bbox[0] and ln.bbox[2] <= x1 + 0.5
                assert y0 - 0.5 <= ln.bbox[1] and ln.bbox[3] <= y1 + 0.5
    # The bbox we store for a phrase is where PyMuPDF finds it on the original page.
    target = next(b for b in doc.pages[2].blocks if "only appears on page 3" in b.text)
    (hit,) = src[2].search_for("Content that only appears on page 3")
    assert abs(hit.y0 - target.bbox[1]) < 3 and abs(hit.x0 - target.bbox[0]) < 3


def test_raw_page_text_is_preserved(policy_pdf):
    doc = _ingest(policy_pdf)
    src = pymupdf.open(policy_pdf)
    for page in doc.pages:
        assert page.raw_text == src[page.page_number - 1].get_text("text")


def test_blank_page_is_kept_and_reported(tmp_path):
    path = make_pdf(tmp_path / "c" / "gap.pdf", [[body(100, "First page text here.")], [], [body(100, "Third page.")]])
    doc = _ingest(path)
    assert [p.page_number for p in doc.pages] == [1, 2, 3]
    assert doc.pages[1].blocks == []
    assert any(w.code == "blank_page" and w.page == 2 for w in doc.warnings)
    assert "Third page." in doc.pages[2].blocks[0].text


def test_image_only_page_is_flagged_as_needing_ocr(tmp_path):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), 0)
    pix.clear_with(200)
    src = pymupdf.open()
    page = src.new_page()
    page.insert_image(pymupdf.Rect(50, 50, 250, 250), pixmap=pix)
    path = tmp_path / "c" / "scan.pdf"
    path.parent.mkdir(parents=True)
    src.save(path)
    doc = _ingest(path)
    assert any(w.code == "no_text_layer" and w.page == 1 for w in doc.warnings)


def test_corrupt_pdf_fails_gracefully(tmp_path):
    path = tmp_path / "c" / "broken.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"%PDF-1.4 this is not really a pdf")
    doc = _ingest(path)
    assert doc.status == "failed"
    assert doc.warnings[0].code == "open_failed"
    assert doc.source.original_filename == "broken.pdf"
    assert len(doc.source.file_sha256) == 64
