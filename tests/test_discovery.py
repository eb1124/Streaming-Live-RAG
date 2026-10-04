import pytest

from ingestion.discovery import discover_pdfs, make_doc_id, slugify
from tests.conftest import body, make_pdf


def test_discovers_pdfs_recursively_and_ignores_other_files(tmp_path):
    corpus = tmp_path / "corpus"
    make_pdf(corpus / "b.pdf", [[body(100, "b")]])
    make_pdf(corpus / "sub" / "deeper" / "a.PDF", [[body(100, "a")]])
    (corpus / "notes.txt").write_text("not a pdf")
    (corpus / "sub" / "image.png").write_bytes(b"\x89PNG")

    found = discover_pdfs(corpus)

    assert [f.relative_path for f in found] == ["b.pdf", "sub/deeper/a.PDF"]
    assert [f.original_filename for f in found] == ["b.pdf", "a.PDF"]


def test_original_filename_preserved_with_unicode(tmp_path):
    name = "5002 Remote Work Policy _ It’s Your Yale.pdf"
    make_pdf(tmp_path / name, [[body(100, "x")]])
    (f,) = discover_pdfs(tmp_path)
    assert f.original_filename == name
    assert f.doc_id.startswith("5002-remote-work-policy-its-your-yale-")


def test_doc_ids_are_stable_and_unique(tmp_path):
    corpus = tmp_path / "corpus"
    for name in ["Policy A.pdf", "policy-a.pdf", "x/Policy A.pdf"]:
        make_pdf(corpus / name, [[body(100, name)]])
    first = [f.doc_id for f in discover_pdfs(corpus)]
    second = [f.doc_id for f in discover_pdfs(corpus)]
    assert first == second
    assert len(set(first)) == 3  # same slug, different paths -> different ids


def test_doc_id_does_not_change_when_content_changes(tmp_path):
    path = make_pdf(tmp_path / "doc.pdf", [[body(100, "version one")]])
    before = discover_pdfs(tmp_path)[0].doc_id
    make_pdf(path, [[body(100, "version two, different bytes")]])
    assert discover_pdfs(tmp_path)[0].doc_id == before


def test_doc_id_format():
    doc_id = make_doc_id("sub/Travel Policy (2026) – Final.pdf")
    slug, digest = doc_id.rsplit("-", 1)
    assert slug == "travel-policy-2026-final"
    assert len(digest) == 6
    assert make_doc_id("sub/Travel Policy (2026) – Final.pdf") == doc_id


def test_slug_truncates_on_word_boundary():
    slug = slugify("a" * 10 + " " + "b" * 60)
    assert len(slug) <= 48 and not slug.endswith("-")


def test_missing_corpus_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        discover_pdfs(tmp_path / "nope")
