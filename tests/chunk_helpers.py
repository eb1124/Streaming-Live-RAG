"""Build small ingestion Documents in memory for chunking tests (no PDF needed)."""

from __future__ import annotations

from ingestion.models import Block, Document, Line, MetaField, Page, SourceFile, TableData
from ingestion.structure import match_marker


def blk(kind: str, text: str, page: int = 1, level: int | None = None, x: float = 72.0, **kw) -> dict:
    return {"kind": kind, "text": text, "page": page, "level": level, "x": x, **kw}


def H(text, level=1, page=1, **kw):
    return blk("heading", text, page, level, **kw)


def P(text, page=1, **kw):
    return blk("paragraph", text, page, **kw)


def L(text, page=1, **kw):
    return blk("list_item", text, page, **kw)


def T(rows, page=1, **kw):
    return blk("table", "\n".join(" | ".join(c or "" for c in r) for r in rows), page, table=rows, **kw)


def make_doc(specs: list[dict], n_pages: int | None = None, doc_id: str = "test-doc", **meta) -> Document:
    n_pages = n_pages or max(s["page"] for s in specs)
    pages = {p: Page(page_number=p, width=612, height=792, raw_text="", lines=[], blocks=[]) for p in range(1, n_pages + 1)}
    y: dict[int, float] = {}
    for s in specs:
        page = pages[s["page"]]
        i = len(page.blocks)
        top = y.get(s["page"], 72.0)
        y[s["page"]] = top + 20
        line_id = f"p{s['page']}-l{len(page.lines)}"
        page.lines.append(Line(line_id=line_id, page=s["page"], bbox=(s["x"], top, 540, top + 12), raw_text=s["text"],
                               text=s["text"], font="helv", size=11, bold_ratio=0))
        marker = match_marker(s["text"]) if s["kind"] in ("heading", "list_item") else None
        if "marker" in s:  # e.g. a gutter number attached by ingestion ("1" with no period)
            marker = ("ordered", s["marker"], s["marker"].strip("().")) if s["marker"] else None
        block = Block(
            block_id=f"p{s['page']}-b{i}", page=s["page"], kind=s["kind"], text=s["text"],
            bbox=(s["x"], top, 540, top + 12), line_ids=[line_id], size=s.get("size", 11.0),
            heading_level=s["level"] if s["kind"] == "heading" else None,
            list_type=s.get("list_type", marker[0] if marker and s["kind"] == "list_item" else None),
            marker=marker[1] if marker else None, number=marker[2] if marker else None,
            table=TableData(rows=s["table"]) if s.get("table") else None,
            confidence=s.get("confidence", "high"), flags=list(s.get("flags", [])),
        )
        page.blocks.append(block)
        page.raw_text += s["text"] + "\n"
    doc = Document(
        pipeline_version="test", doc_id=doc_id,
        source=SourceFile(original_filename=f"{doc_id}.pdf", relative_path=f"{doc_id}.pdf", file_sha256="0" * 64, file_size_bytes=1),
        pages=list(pages.values()),
    )
    doc.metadata.title = MetaField(value=meta.get("title", "Test Policy"), source="document_text")
    doc.metadata.organization = MetaField(value=meta.get("organization", "Test University"), source="override")
    for name in ("domain", "document_type", "authority_level", "version", "series_id"):
        if name in meta:
            setattr(doc.metadata, name, MetaField(value=meta[name], source="curated"))
    if "effective_date" in meta:
        doc.metadata.effective_date = MetaField(value=meta["effective_date"], normalized=meta["effective_date"])
    return doc


def sentence(i: int, words: int = 18) -> str:
    return f"Sentence {i} states that " + " ".join(f"word{i}x{k}" for k in range(words)) + "."
