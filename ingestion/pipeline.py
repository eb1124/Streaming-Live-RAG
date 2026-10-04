"""Orchestrates ingestion of one document and of the whole corpus."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import PIPELINE_VERSION
from .clean import clean_pages
from .discovery import DiscoveredFile, discover_pdfs, file_sha256
from .extract import ExtractedPage, extract_page, open_pdf, read_pdf_info
from .metadata import extract_metadata
from .models import Document, Page, SourceFile
from .profiles import build_profile, detect_profile_name, load_overrides
from .structure import (
    _learn_para_gap,
    assign_levels_and_sections,
    attach_gutter_numbers,
    build_blocks,
    classify_blocks,
    compute_style,
    demote_heading_runs,
    detect_implicit_lists,
    exclude_sections,
    flag_site_chrome,
    is_chrome,
    is_retrievable,
    order_page,
)

NEAR_EMPTY_CHARS = 40


def ingest_file(f: DiscoveredFile, override: dict | None = None) -> Document:
    doc = Document(
        pipeline_version=PIPELINE_VERSION,
        doc_id=f.doc_id,
        source=SourceFile(
            original_filename=f.original_filename,
            relative_path=f.relative_path,
            file_sha256=file_sha256(f.path),
            file_size_bytes=f.path.stat().st_size,
        ),
    )
    try:
        pdf = open_pdf(f.path)
    except Exception as exc:  # corrupt / not a PDF
        doc.status = "failed"
        doc.warn("open_failed", f"{type(exc).__name__}: {exc}", "error")
        return doc

    with pdf:
        if pdf.needs_pass:
            doc.status = "failed"
            doc.warn("encrypted", "PDF is password protected", "error")
            return doc
        doc.pdf = read_pdf_info(pdf)
        profile = build_profile(detect_profile_name(doc.pdf), override)
        doc.profile = profile.name
        pages: list[ExtractedPage] = []
        for page in pdf:
            try:
                pages.append(extract_page(page, profile.calibri_fonts))
            except Exception as exc:
                doc.warn("page_extract_failed", f"{type(exc).__name__}: {exc}", "error", page.number + 1)
                pages.append(
                    ExtractedPage(page.number + 1, page.rect.width, page.rect.height, page.rotation, 0, "", [])
                )

    removed = clean_pages(pages, profile)
    style = compute_style(pages)
    ordered = [order_page(p, style, doc) for p in pages]
    style.para_gap = _learn_para_gap(ordered, style)
    for p, atoms in zip(pages, ordered):
        blocks = build_blocks(p.page_number, atoms, style)
        classify_blocks(blocks, style)
        blocks = attach_gutter_numbers(blocks)
        doc.pages.append(
            Page(
                page_number=p.page_number,
                width=p.width,
                height=p.height,
                rotation=p.rotation,
                image_count=p.image_count,
                raw_text=p.raw_text,
                lines=p.lines,
                blocks=blocks,
            )
        )
    all_blocks = list(doc.iter_blocks())
    demote_heading_runs(all_blocks)
    detect_implicit_lists(all_blocks, style)
    extract_metadata(doc, override)
    for note in (override or {}).get("curator_warnings", []):
        doc.warn("curator_flag", note)
    if profile.flag_site_chrome:
        flag_site_chrome(doc, doc.metadata.title.value)
    assign_levels_and_sections(doc)
    exclude_sections(doc, (override or {}).get("exclude_sections_from_retrieval", []))

    _page_warnings(doc, pages)
    _repair_warnings(doc, pages, removed)
    doc.stats = _stats(doc, removed)
    doc.content_hash = hashlib.sha256("\n\n".join(b.text for b in doc.iter_blocks()).encode("utf-8")).hexdigest()
    return doc


def _page_warnings(doc: Document, pages: list[ExtractedPage]) -> None:
    for page in doc.pages:
        kept = "".join(b.text for b in page.blocks)
        if not page.raw_text.strip():
            if page.image_count:
                doc.warn("no_text_layer", "page has images but no extractable text (OCR needed?)", "warning", page.page_number)
            else:
                doc.warn("blank_page", "page has no text", "info", page.page_number)
        elif not kept.strip():
            reasons = Counter(ln.removed for ln in page.lines if ln.removed)
            doc.warn(
                "empty_after_cleaning",
                f"all text on page was removed as noise ({dict(reasons)})",
                "warning",
                page.page_number,
            )
        elif len(kept) < NEAR_EMPTY_CHARS:
            doc.warn("near_empty_page", f"only {len(kept)} characters of content: {kept[:40]!r}", "info", page.page_number)


def _repair_warnings(doc: Document, pages: list[ExtractedPage], removed: dict[str, int]) -> None:
    repairs: Counter = Counter()
    for p in pages:
        repairs.update(p.repairs)
    if repairs:
        doc.warn("text_repaired", f"glyph repairs applied: {dict(repairs)}", "info")
    if removed.get("duplicate_render"):
        doc.warn(
            "duplicate_render",
            f"{removed['duplicate_render']} duplicate re-rendered lines (text-shadow effect) removed",
            "info",
        )
    pua = sum(1 for p in doc.pages for ln in p.lines if "private_use_glyphs_removed" in ln.repairs)
    if pua:
        doc.warn("icon_glyphs", f"icon-font glyphs stripped from {pua} lines", "info")


def _stats(doc: Document, removed: dict[str, int]) -> dict[str, int]:
    blocks = list(doc.iter_blocks())
    kinds = Counter(b.kind for b in blocks)
    lists = sum(
        1 for i, b in enumerate(blocks) if b.kind == "list_item" and (i == 0 or blocks[i - 1].kind != "list_item")
    )
    lines = [ln for p in doc.pages for ln in p.lines]
    stats = {
        "pages": len(doc.pages),
        "empty_pages": sum(1 for p in doc.pages if not p.blocks),
        "lines_total": len(lines),
        "lines_kept": sum(1 for ln in lines if not ln.removed),
        "blocks": len(blocks),
        "headings": kinds["heading"],
        "paragraphs": kinds["paragraph"],
        "list_items": kinds["list_item"],
        "lists": lists,
        "tables": kinds["table"],
        "sections": len(doc.sections),
        "chrome_flagged_blocks": sum(1 for b in blocks if is_chrome(b)),
        "retrieval_excluded_blocks": sum(1 for b in blocks if not is_retrievable(b)),
        "chars_raw": sum(len(p.raw_text) for p in doc.pages),
        "chars_clean": sum(len(b.text) for b in blocks),
    }
    stats.update({f"removed_{k}": v for k, v in sorted(removed.items())})
    return stats


def run(corpus_dir: Path, out_dir: Path, overrides_file: Path | None = None) -> list[Document]:
    files = discover_pdfs(corpus_dir)
    overrides = load_overrides(overrides_file) if overrides_file else {}
    unknown = set(overrides) - {f.original_filename for f in files}
    before = {f.relative_path: file_sha256(f.path) for f in files}

    docs = [ingest_file(f, overrides.get(f.original_filename)) for f in files]

    after = {f.relative_path: file_sha256(f.path) for f in files}
    if before != after:  # we never write to corpus/; this guards against it ever happening
        raise RuntimeError("Corpus files changed during ingestion")

    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.json"):
        stale.unlink()
    for doc in docs:
        (out_dir / f"{doc.doc_id}.json").write_text(doc.model_dump_json(indent=1), encoding="utf-8")
    manifest = {
        "pipeline_version": PIPELINE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus_dir": str(corpus_dir),
        "unmatched_overrides": sorted(unknown),
        "documents": [manifest_entry(d) for d in docs],
    }
    (out_dir / "_manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    return docs


MANIFEST_FIELDS = (
    "title", "organization", "domain", "document_type", "version", "effective_date",
    "revision_date", "issued_date", "source_url", "captured_at", "authority_level",
    "series_id", "superseded_date", "is_current",
)


def manifest_entry(d: Document) -> dict:
    """One corpus-manifest row. Unknown values are explicit nulls and listed in missing_metadata."""
    md = d.metadata
    values = {name: getattr(md, name).value for name in MANIFEST_FIELDS}
    provenance = {
        name: getattr(md, name).model_dump(exclude_none=True, exclude={"value"})
        for name in MANIFEST_FIELDS
        if values[name] is not None
    }
    return {
        "doc_id": d.doc_id,
        "filename": d.source.original_filename,
        "status": d.status,
        **{k: v for k, v in values.items() if k != "captured_at"},
        "capture_date": values["captured_at"],
        "page_count": d.pdf.page_count if d.pdf else None,
        "file_sha256": d.source.file_sha256,
        "content_hash": d.content_hash,
        "profile": d.profile,
        "metadata_provenance": provenance,
        "missing_metadata": [k for k, v in values.items() if v is None],
        "warnings": [f"{w.code}{f' p{w.page}' if w.page else ''}: {w.message}" for w in d.warnings if w.severity != "info"],
        "info": sorted({w.code for w in d.warnings if w.severity == "info"}),
        "retrieval_excluded_blocks": d.stats.get("retrieval_excluded_blocks", 0),
    }


def load_documents(out_dir: Path) -> list[Document]:
    return [
        Document.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted(out_dir.glob("*.json"))
        if not p.name.startswith("_")
    ]
