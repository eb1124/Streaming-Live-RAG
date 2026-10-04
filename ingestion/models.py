"""Normalized intermediate representation written to data/ingested/<doc_id>.json.

Document
  ├── source / pdf          file identity, hashes, PDF-level info
  ├── metadata              each field carries value + provenance (+ evidence)
  ├── pages[]
  │     ├── raw_text        PyMuPDF plain text, untouched (reversibility)
  │     ├── lines[]         every visual line, incl. removed ones (with reason)
  │     └── blocks[]        heading | paragraph | list_item | table, in reading order
  ├── sections[]            heading hierarchy (flat list with parent ids)
  └── warnings[]

Coordinates are PDF points in the page's coordinate space (origin top-left),
so any block/line can be located on the original PDF page.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1"

BBox = tuple[float, float, float, float]
BlockKind = Literal["heading", "paragraph", "list_item", "table"]


class Warning_(BaseModel):
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"
    page: int | None = None


class SourceFile(BaseModel):
    original_filename: str
    relative_path: str  # POSIX path relative to the corpus root
    file_sha256: str
    file_size_bytes: int


class PdfInfo(BaseModel):
    page_count: int
    producer: str | None = None
    creator: str | None = None
    pdf_title: str | None = None
    author: str | None = None
    creation_date: str | None = None
    modification_date: str | None = None
    encrypted: bool = False


class MetaField(BaseModel):
    """A metadata value plus where it came from. ``value is None`` means unknown."""

    value: str | None = None
    source: str | None = None  # pdf_metadata | browser_header | browser_footer | document_text | override
    page: int | None = None
    evidence: str | None = None  # the extracted text that supports the value
    normalized: str | None = None  # e.g. ISO date, when unambiguous
    note: str | None = None  # qualification / curator basis (e.g. "organization named only indirectly")


class LabeledField(BaseModel):
    """A 'Label: value' pair found on the first pages (e.g. 'Effective Date: May 1, 2026')."""

    label: str
    value: str
    page: int


class DocumentMetadata(BaseModel):
    title: MetaField = Field(default_factory=MetaField)
    organization: MetaField = Field(default_factory=MetaField)
    domain: MetaField = Field(default_factory=MetaField)
    source_url: MetaField = Field(default_factory=MetaField)
    version: MetaField = Field(default_factory=MetaField)
    effective_date: MetaField = Field(default_factory=MetaField)
    revision_date: MetaField = Field(default_factory=MetaField)
    issued_date: MetaField = Field(default_factory=MetaField)  # "Issued on" / "Publication Date"
    captured_at: MetaField = Field(default_factory=MetaField)  # browser print / capture timestamp
    document_type: MetaField = Field(default_factory=MetaField)  # policy | procedure | standard | guidance
    authority_level: MetaField = Field(default_factory=MetaField)  # authoritative | official_web | informational
    # Document identity across versions (curated; basis required). Groundwork for temporal
    # retrieval: versions of one document share a series_id and differ in effective_date.
    series_id: MetaField = Field(default_factory=MetaField)  # e.g. "uconn-travel-entertainment-procedures"
    superseded_date: MetaField = Field(default_factory=MetaField)  # date a newer version took effect
    is_current: MetaField = Field(default_factory=MetaField)  # "true" | "false" as of the curation date; null = unknown
    labeled_fields: list[LabeledField] = Field(default_factory=list)


class Line(BaseModel):
    """One visual line. Never deleted: noise is marked via ``removed``."""

    line_id: str
    page: int
    bbox: BBox
    raw_text: str  # as extracted (after span joining), before normalization
    text: str  # normalized (ligatures repaired, icon glyphs stripped, whitespace collapsed)
    font: str
    size: float
    bold_ratio: float
    removed: str | None = None  # reason if excluded from clean content
    repairs: list[str] = Field(default_factory=list)


class TableData(BaseModel):
    rows: list[list[str | None]]
    source: str = "pymupdf.find_tables"


class Block(BaseModel):
    block_id: str
    page: int
    kind: BlockKind
    text: str
    bbox: BBox
    line_ids: list[str]
    size: float  # dominant font size (pt)
    bold_ratio: float = 0.0  # share of characters set in a bold face
    line_count: int = 1
    heading_level: int | None = None
    list_type: Literal["bullet", "ordered", "implicit"] | None = None
    marker: str | None = None  # the literal bullet / enumerator, e.g. "•", "3.2.1.", "(a)"
    number: str | None = None  # enumerator without punctuation, e.g. "3.2.1", "PR1.1"
    section_id: str | None = None
    table: TableData | None = None
    confidence: Literal["high", "medium", "low"] = "high"
    flags: list[str] = Field(default_factory=list)


class Page(BaseModel):
    page_number: int  # 1-based physical page index in the PDF
    width: float
    height: float
    rotation: int = 0
    image_count: int = 0
    raw_text: str
    lines: list[Line] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)


class Section(BaseModel):
    section_id: str
    title: str
    level: int
    number: str | None = None
    parent_id: str | None = None
    page_start: int
    heading_block_id: str


class Document(BaseModel):
    schema_version: str = SCHEMA_VERSION
    pipeline_version: str
    doc_id: str
    status: Literal["ok", "failed"] = "ok"
    profile: str | None = None
    source: SourceFile
    pdf: PdfInfo | None = None
    metadata: DocumentMetadata = Field(default_factory=DocumentMetadata)
    content_hash: str | None = None  # sha256 of the cleaned block text
    pages: list[Page] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    warnings: list[Warning_] = Field(default_factory=list)
    stats: dict[str, int] = Field(default_factory=dict)

    def iter_blocks(self):
        for page in self.pages:
            yield from page.blocks

    def warn(self, code: str, message: str, severity: str = "warning", page: int | None = None) -> None:
        self.warnings.append(Warning_(code=code, message=message, severity=severity, page=page))
