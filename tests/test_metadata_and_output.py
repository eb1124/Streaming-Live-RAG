"""Metadata provenance, overrides, serialization, and output location."""

import json

from ingestion.discovery import discover_pdfs, file_sha256
from ingestion.metadata import normalize_date, strip_tracking
from ingestion.models import Document
from ingestion.pipeline import ingest_file, load_documents, run
from tests.conftest import body, heading, make_pdf


def _ingest(path, override=None):
    (f,) = [f for f in discover_pdfs(path.parent) if f.path == path]
    return ingest_file(f, override)


def test_unknown_metadata_stays_null(tmp_path):
    doc = _ingest(make_pdf(tmp_path / "c" / "plain.pdf", [[body(100, "Some text without any labels.")]]))
    md = doc.metadata
    assert md.organization.value is None and md.organization.source is None
    assert md.domain.value is None
    assert md.source_url.value is None
    assert md.effective_date.value is None
    assert {w.message.split()[0] for w in doc.warnings if w.code == "metadata_unknown"} >= {"organization", "domain"}


def test_placeholder_pdf_title_is_rejected(tmp_path):
    paragraph = [body(140 + 14 * i, "Body text of the procedure that is long enough to be the dominant size.") for i in range(5)]
    path = make_pdf(tmp_path / "c" / "p.pdf", [[heading(100, "Real Heading Title", 20)] + paragraph], title="Month XX, XXXX")
    doc = _ingest(path)
    assert doc.metadata.title.value == "Real Heading Title"
    assert doc.metadata.title.source == "document_text"
    assert any(w.code == "pdf_title_placeholder" for w in doc.warnings)


def test_override_applied_only_with_evidence(tmp_path):
    path = make_pdf(tmp_path / "c" / "o.pdf", [[body(100, "Reimbursed by Acme College finance office.")]])
    good = _ingest(path, {"organization": {"value": "Acme College", "evidence": "Acme College finance"},
                          "domain": {"value": "travel_expenses", "basis": "title"}})
    assert good.metadata.organization.value == "Acme College"
    assert good.metadata.organization.source == "override"
    assert good.metadata.domain.value == "travel_expenses"

    bad = _ingest(path, {"organization": {"value": "Globex University", "evidence": "Globex University"}})
    assert bad.metadata.organization.value is None
    assert any(w.code == "override_rejected" for w in bad.warnings)


def test_override_drop_patterns(tmp_path):
    path = make_pdf(tmp_path / "c" / "d.pdf", [[body(100, "Accept Cookies"), body(130, "Real policy sentence.")]])
    doc = _ingest(path, {"drop_line_patterns": ["Accept Cookies"]})
    assert "Accept Cookies" not in " ".join(b.text for b in doc.iter_blocks())
    assert any(ln.removed == "override_pattern" for ln in doc.pages[0].lines)


def test_document_json_roundtrip(policy_pdf):
    doc = _ingest(policy_pdf)
    restored = Document.model_validate_json(doc.model_dump_json())
    assert restored == doc
    data = json.loads(doc.model_dump_json())
    assert data["source"]["original_filename"] == "policy.pdf"
    assert data["pages"][0]["page_number"] == 1
    assert data["metadata"]["title"]["value"] == "Travel Policy"
    assert data["metadata"]["title"]["source"] in {"pdf_metadata", "document_text"}


def test_hashes(policy_pdf):
    doc = _ingest(policy_pdf)
    assert doc.source.file_sha256 == file_sha256(policy_pdf)
    assert doc.content_hash and len(doc.content_hash) == 64
    assert _ingest(policy_pdf).content_hash == doc.content_hash  # deterministic


def test_run_writes_outside_corpus_and_never_modifies_it(policy_pdf, tmp_path):
    corpus = policy_pdf.parent
    make_pdf(corpus / "second.pdf", [[body(100, "Second document.")]])
    before = {p.name: (p.stat().st_mtime_ns, file_sha256(p)) for p in corpus.iterdir()}
    out = tmp_path / "data" / "ingested"

    docs = run(corpus, out)

    after = {p.name: (p.stat().st_mtime_ns, file_sha256(p)) for p in corpus.iterdir()}
    assert before == after  # no new, changed or touched files in corpus/
    assert sorted(p.name for p in out.glob("*.json")) == sorted([f"{d.doc_id}.json" for d in docs] + ["_manifest.json"])
    manifest = json.loads((out / "_manifest.json").read_text(encoding="utf-8"))
    assert {d["filename"] for d in manifest["documents"]} == {"policy.pdf", "second.pdf"}
    assert [d.doc_id for d in load_documents(out)] == sorted(d.doc_id for d in docs)


def test_failed_document_does_not_stop_the_run(policy_pdf, tmp_path):
    (policy_pdf.parent / "broken.pdf").write_bytes(b"garbage")
    docs = run(policy_pdf.parent, tmp_path / "out")
    assert sorted(d.status for d in docs) == ["failed", "ok"]


def test_helpers():
    assert strip_tracking("https://x.edu/p/?utm_source=chatgpt.com&id=3") == "https://x.edu/p/?id=3"
    assert strip_tracking("https://x.edu/p?utm_source=chatgpt.com") == "https://x.edu/p"
    assert normalize_date("August 31, 2022") == "2022-08-31"
    assert normalize_date("September, 1990") == "1990-09"
    assert normalize_date("2026-07-15") == "2026-07-15"
    assert normalize_date("03/2026") == "2026-03"
    assert normalize_date("sometime soon") is None


def test_filename_is_not_accepted_as_evidence(tmp_path):
    path = make_pdf(tmp_path / "c" / "Globex University Travel.pdf", [[body(100, "Travel reimbursement rules apply to all staff.")]])
    doc = _ingest(path, {"organization": {"value": "Globex University", "evidence": "Globex University"}})
    assert doc.metadata.organization.value is None
    assert any(w.code == "override_rejected" for w in doc.warnings)


def test_curated_fields_need_a_basis_and_valid_evidence(tmp_path):
    path = make_pdf(tmp_path / "c" / "g.pdf", [[body(100, "See the separate Travel Policy for rules.")]])
    doc = _ingest(path, {
        "document_type": {"value": "guidance", "basis": "points to a separate policy", "evidence": "See the separate Travel Policy"},
        "authority_level": {"value": "informational"},  # no basis -> rejected
        "domain": {"value": "procurement", "basis": "x", "evidence": "purchase orders"},  # evidence absent -> rejected
        "captured_at": {"value": "2026-09-25", "basis": "download date"},
    })
    md = doc.metadata
    assert (md.document_type.value, md.document_type.source, md.document_type.note) == ("guidance", "curated", "points to a separate policy")
    assert md.authority_level.value is None and md.domain.value is None
    assert md.captured_at.normalized == "2026-09-25"
    assert sum(w.code == "override_rejected" for w in doc.warnings) == 2


def test_note_preserves_indirect_provenance(tmp_path):
    path = make_pdf(tmp_path / "c" / "m.pdf", [[body(100, "Only McGill students may request advances.")]])
    doc = _ingest(path, {"organization": {"value": "McGill University", "evidence": "McGill students", "note": "INDIRECT"}})
    org = doc.metadata.organization
    assert (org.value, org.source, org.evidence, org.note) == ("McGill University", "override", "McGill students", "INDIRECT")


def test_authoring_filename_pdf_title_is_ignored(tmp_path):
    paragraph = [body(140 + 14 * i, "Body text of the standard that is long enough to be the dominant size.") for i in range(5)]
    path = make_pdf(tmp_path / "c" / "t.pdf", [[heading(100, "Procurement Thresholds", 20)] + paragraph],
                    title="03-010 Procurement - 5.10.18_ph comments.JBEditsdocx.docx")
    assert _ingest(path).metadata.title.value == "Procurement Thresholds"


def test_excluded_sections_are_flagged_not_deleted(tmp_path):
    pages = [[heading(100, "Policy", 18), body(130, "Rule text that must stay retrievable for everyone."),
              heading(170, "Revision History", 18), body(200, "01/01/2020 Added section 4."), body(230, "02/02/2021 Clarified section 5.")]]
    doc = _ingest(make_pdf(tmp_path / "c" / "r.pdf", pages),
                  {"exclude_sections_from_retrieval": [{"title": "Revision History", "reason": "unreliable"}]})
    from ingestion.structure import is_retrievable
    def block(text):
        return next(b for b in doc.iter_blocks() if text in b.text)

    assert is_retrievable(block("Rule text that must stay retrievable"))
    assert not is_retrievable(block("01/01/2020 Added section 4."))
    assert "exclude_from_retrieval:unreliable" in block("02/02/2021 Clarified section 5.").flags
    assert not is_retrievable(block("Revision History"))
    assert "02/02/2021 Clarified section 5." in " ".join(b.text for b in doc.iter_blocks())  # still present


def test_manifest_entry_has_every_field_with_explicit_nulls(policy_pdf):
    from ingestion.pipeline import manifest_entry
    entry = manifest_entry(_ingest(policy_pdf))
    for key in ["doc_id", "filename", "title", "organization", "domain", "document_type", "version", "effective_date",
                "revision_date", "source_url", "capture_date", "page_count", "content_hash", "metadata_provenance",
                "warnings", "authority_level", "missing_metadata"]:
        assert key in entry, key
    assert entry["organization"] is None and "organization" in entry["missing_metadata"]
    assert entry["metadata_provenance"]["effective_date"]["source"] == "document_text"


def test_issued_on_label_becomes_issued_date():
    from ingestion.metadata import LABELS, normalize_date

    assert LABELS["issued on"] == LABELS["publication date"] == "issued_date"
    assert normalize_date("03/2026") == "2026-03"
