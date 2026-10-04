"""The repository's document metadata, read-only (phase 8): what metadata_lookup reports and what document_search
attaches to every result. Nothing here is derived or guessed; every value is read from

  data/ingested/_manifest.json     per document: file, title, organization, dates, status, provenance (ingestion)
  data/chunks/*.jsonl              per chunk: the metadata copied from ingestion, pages, sections, clauses, text
  data/chunks/_manifest.json       chunker version, retrievable / excluded counts per document
  config/organization_aliases.toml short organization names (the phase 2 aliases)

and versions come from temporal.versions.VersionRegistry (series_id, effective_date, superseded_date, is_current),
the registry the frozen temporal resolution uses.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from chunking.models import Chunk
from temporal.versions import VersionRegistry

from .contracts import ChunkMetadata, CorpusInfo, DocumentMetadata, DocumentRef, Location


def _iso(value: str | None) -> date | None:
    """A full ISO date, else None (month-only or raw values give no usable boundary; as temporal/versions.py)."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


class MetadataCatalog:
    def __init__(self, chunks: list[Chunk], ingested: dict, chunk_manifest: dict | None = None,
                 aliases: dict[str, str] | None = None):
        self.chunks = list(chunks)
        self.by_id = {c.chunk_id: c for c in self.chunks}
        self.by_doc: dict[str, list[Chunk]] = {}
        for c in self.chunks:
            self.by_doc.setdefault(c.doc_id, []).append(c)
        self.ingested = {d["doc_id"]: d for d in ingested.get("documents", [])}
        self.ingestion_version = ingested.get("pipeline_version")
        chunk_manifest = chunk_manifest or {}
        self.chunker_version = chunk_manifest.get("chunker_version")
        self.excluded = {d["doc_id"]: d.get("excluded_records", 0) for d in chunk_manifest.get("documents", [])}
        self.organizations = sorted({c.organization for c in self.chunks if c.organization}
                                    | {d["organization"] for d in self.ingested.values() if d.get("organization")})
        self.aliases = dict(aliases or {})
        self.registry = VersionRegistry.from_chunks(self.chunks)

    @classmethod
    def load(cls) -> "MetadataCatalog":
        from adaptive.multi.decompose import load_aliases
        from chunking.pipeline import load_chunks, load_manifest
        from ingestion.config import OUTPUT_DIR

        chunks = [c for cs in load_chunks().values() for c in cs]
        ingested = json.loads(Path(OUTPUT_DIR, "_manifest.json").read_text(encoding="utf-8"))
        return cls(chunks, ingested, load_manifest(), load_aliases())

    # ------------------------------------------------------------ names

    def resolve_organization(self, name: str) -> str | None:
        """A corpus organization from its full name or a configured short name (case-insensitive), else None."""
        key = " ".join(name.split()).casefold()
        for org in self.organizations:
            if org.casefold() == key:
                return org
        for alias, org in self.aliases.items():
            if alias.casefold() == key and org in self.organizations:
                return org
        return None

    def doc_ids(self) -> list[str]:
        return sorted(set(self.ingested) | set(self.by_doc))

    # ------------------------------------------------------------ versions

    def applicability(self, chunk: Chunk, day: date) -> str:
        """Whether the chunk's document applies on `day`, from its metadata only:

          "in_force"      a version of a multi-version series in force that day (VersionRegistry.in_force, the frozen
                          temporal rule), or a standalone document with a full effective date on or before `day` and
                          no full superseded date on or before it
          "not_in_force"  a series version not in force that day; a standalone document with a full effective date
                          after `day` or a full superseded date on or before it
          "undetermined"  a standalone document without a full (ISO) effective date (missing or month-only), and not
                          superseded by `day`: applicability cannot be established from metadata
        """
        series = self.registry.series_of.get(chunk.doc_id)
        if series is not None:
            in_force = chunk.doc_id in {v.doc_id for v in self.registry.in_force(series, day)}
            return "in_force" if in_force else "not_in_force"
        effective, superseded = _iso(chunk.effective_date), _iso(chunk.superseded_date)
        if (effective is not None and effective > day) or (superseded is not None and superseded <= day):
            return "not_in_force"
        return "in_force" if effective is not None else "undetermined"

    def in_force(self, chunk: Chunk, day: date) -> bool:
        """The as_of filter: False only when the metadata shows the document does not apply on `day`
        (undetermined documents stay eligible)."""
        return self.applicability(chunk, day) != "not_in_force"

    def series_versions(self, doc_id: str) -> list[str]:
        series = self.registry.series_of.get(doc_id)
        return [v.doc_id for v in self.registry.by_series[series]] if series else []

    # ------------------------------------------------------------ records

    def document_ref(self, chunk: Chunk) -> DocumentRef:
        m = self.ingested.get(chunk.doc_id, {})
        return DocumentRef(doc_id=chunk.doc_id, title=chunk.title, filename=m.get("filename"),
                           organization=chunk.organization, domain=chunk.domain, document_type=chunk.document_type,
                           authority_level=chunk.authority_level, version=chunk.version,
                           effective_date=chunk.effective_date, superseded_date=chunk.superseded_date,
                           is_current=chunk.is_current, series_id=chunk.series_id, source_url=chunk.source_url,
                           page_count=m.get("page_count"))

    @staticmethod
    def location(chunk: Chunk) -> Location:
        return Location(page_start=chunk.page_start, page_end=chunk.page_end, pages=list(chunk.pages),
                        section_path=list(chunk.section_path), clause_id=chunk.clause_id,
                        clause_ids=list(chunk.clause_ids), source_block_ids=list(chunk.source_block_ids))

    def chunk_metadata(self, chunk: Chunk, include_text: bool = False) -> ChunkMetadata:
        return ChunkMetadata(chunk_id=chunk.chunk_id, doc_id=chunk.doc_id, ordinal=chunk.ordinal,
                             location=self.location(chunk), prev_chunk_id=chunk.prev_chunk_id,
                             next_chunk_id=chunk.next_chunk_id, split=chunk.split, token_count=chunk.token_count,
                             confidence=chunk.confidence, content_hash=chunk.content_hash,
                             text=chunk.text if include_text else None)

    def document(self, doc_id: str, include_provenance: bool = False) -> DocumentMetadata:
        m = self.ingested.get(doc_id, {})
        cs = self.by_doc.get(doc_id, [])
        c = cs[0] if cs else None

        def pick(field: str):  # the chunks' (normalized) value when there are chunks, else the manifest's
            return getattr(c, field) if c is not None else m.get(field)

        is_current = c.is_current if c is not None else {"true": True, "false": False}.get(str(m.get("is_current")))
        return DocumentMetadata(
            doc_id=doc_id, filename=m.get("filename", ""), status=m.get("status", "unknown"),
            title=pick("title"), organization=pick("organization"), domain=pick("domain"),
            document_type=pick("document_type"), authority_level=pick("authority_level"), version=pick("version"),
            effective_date=pick("effective_date"), effective_date_text=m.get("effective_date"),
            revision_date=m.get("revision_date"), issued_date=pick("issued_date"),
            superseded_date=pick("superseded_date"), is_current=is_current, series_id=pick("series_id"),
            series_versions=self.series_versions(doc_id), source_url=pick("source_url"),
            capture_date=m.get("capture_date") if c is None else c.capture_date, page_count=m.get("page_count"),
            file_sha256=m.get("file_sha256"), content_hash=m.get("content_hash"), chunks=len(cs),
            excluded_records=self.excluded.get(doc_id, 0), missing_metadata=list(m.get("missing_metadata", [])),
            provenance=m.get("metadata_provenance") if include_provenance else None)

    def corpus_info(self) -> CorpusInfo:
        return CorpusInfo(documents=len(self.doc_ids()), chunks=len(self.chunks), organizations=self.organizations,
                          ingestion_version=self.ingestion_version, chunker_version=self.chunker_version)
