"""Chunk records written to data/chunks/<doc_id>.jsonl (retrievable) and
data/chunks/excluded/<doc_id>.jsonl (preserved, never indexed)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]
# How the chunk's boundaries were produced, from most to least structural.
SplitKind = Literal[
    "structural",  # exactly one structural unit (section, clause, or a section's own text)
    "grouped",  # several small sibling units under one parent
    "paragraph",  # an oversized unit cut at paragraph boundaries
    "list",  # ... at list-item boundaries
    "sentence",  # ... at sentence boundaries (a single block was too big)
    "token",  # ... at word boundaries (a single sentence was too big; last resort)
    "table_rows",  # an oversized table cut between rows (header row repeated as context)
    "excluded",  # a record of non-retrievable content
]


class TableRef(BaseModel):
    block_id: str
    rows: list[list[str | None]]  # as extracted by ingestion (sub-range if split by rows)
    header_row: list[str | None] | None = None  # repeated here when the table was split by rows
    confidence: Confidence
    notes: list[str] = Field(default_factory=list)


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    ordinal: int  # position in the document's chunk sequence (retrievable and excluded separately)

    # Document identity (copied from ingestion metadata; never invented; null = unknown)
    organization: str | None
    title: str | None
    domain: str | None
    document_type: str | None
    authority_level: str | None
    version: str | None
    issued_date: str | None
    effective_date: str | None  # ISO date when ingestion could normalize it, else the raw value
    superseded_date: str | None
    is_current: bool | None
    series_id: str | None
    source_url: str | None
    capture_date: str | None

    # Structure
    section_path: list[str]  # headings and clause labels from the document root to this chunk
    section_ids: list[str]  # ingestion section ids of the blocks in this chunk
    clause_id: str | None  # the innermost clause containing the whole chunk, e.g. "5.1.1", "PR1.1"
    clause_ids: list[str]  # every numbered clause whose text is in this chunk

    # Provenance
    page_start: int
    page_end: int
    pages: list[int]
    source_block_ids: list[str]
    source_line_ids: list[str]
    char_span: tuple[int, int] | None = None  # sub-range of the single source block, for sentence/token splits

    # Sequence (retrievable chunks only)
    prev_chunk_id: str | None = None
    next_chunk_id: str | None = None

    # Content
    text: str  # this chunk's own content, verbatim from ingestion blocks (tables rendered as rows)
    context_header: str  # document title + section path, prepended for indexing
    lead_in: str | None = None  # clause head / list lead-in carried from an earlier chunk of the same unit
    retrieval_text: str  # context_header + lead_in + text: what an index should embed / match
    token_count: int  # tokens in `text`
    retrieval_token_count: int  # tokens in `retrieval_text` (the size limit applies to this)
    content_hash: str  # sha256 of `text`
    tables: list[TableRef] = Field(default_factory=list)

    # Quality
    split: SplitKind
    retrievable: bool = True
    exclusion_reason: str | None = None
    confidence: Confidence = "high"
    confidence_reasons: list[str] = Field(default_factory=list)
