"""Bootstrap load (phase 9): the repository metadata into PostgreSQL.

  source = read_repository()                    data/ingested/_manifest.json, data/chunks (chunking.pipeline loaders)
  rows_of(source)                               pure: the corpus_loads, documents and chunks rows
  load(conn, source)                            one transaction: record the load, replace every document and chunk row

The files under data/ stay the source of truth (retrieval and metadata_lookup read them); the tables are a copy that
can be rebuilt at any time. Document values are chosen as services/mcp/catalog.py (metadata_lookup) chooses them:
the chunk metadata when the document has chunks (normalized dates), else the manifest; tests/test_persistence.py and
evaluation/database check that the two agree. Loading the same files twice gives the same documents and chunks rows
(and one more corpus_loads row). Excluded chunk records and chunk text are not loaded.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DOCUMENT_FIELDS = ("doc_id", "filename", "status", "title", "organization", "domain", "document_type",
                   "authority_level", "version", "effective_date", "effective_on", "effective_date_text",
                   "revision_date", "issued_date", "superseded_date", "superseded_on", "is_current", "series_id",
                   "source_url", "capture_date", "page_count", "file_sha256", "content_hash", "chunk_count",
                   "excluded_records", "missing_metadata", "metadata_provenance")
CHUNK_FIELDS = ("chunk_id", "doc_id", "ordinal", "page_start", "page_end", "pages", "section_path", "clause_id",
                "clause_ids", "split", "token_count", "confidence", "content_hash", "prev_chunk_id", "next_chunk_id")
_FROM_CHUNK = ("title", "organization", "domain", "document_type", "authority_level", "version", "effective_date",
               "issued_date", "superseded_date", "is_current", "series_id", "source_url", "capture_date")


@dataclass(frozen=True)
class RepositoryMetadata:
    ingested: dict  # data/ingested/_manifest.json
    chunks: list  # chunking.models.Chunk, every retrievable chunk
    chunk_manifest: dict  # data/chunks/_manifest.json
    hashes: dict  # {"ingested": sha256, "chunks": sha256} of the two manifest files


def read_repository() -> RepositoryMetadata:
    from chunking.config import CHUNKS_DIR
    from chunking.pipeline import load_chunks, load_manifest
    from ingestion.config import OUTPUT_DIR

    ingested_path, chunks_path = Path(OUTPUT_DIR, "_manifest.json"), Path(CHUNKS_DIR, "_manifest.json")
    return RepositoryMetadata(
        ingested=json.loads(ingested_path.read_text(encoding="utf-8")),
        chunks=[c for cs in load_chunks().values() for c in cs], chunk_manifest=load_manifest(),
        hashes={"ingested": hashlib.sha256(ingested_path.read_bytes()).hexdigest(),
                "chunks": hashlib.sha256(chunks_path.read_bytes()).hexdigest()})


def _iso(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def rows_of(source: RepositoryMetadata) -> tuple[dict, list[dict], list[dict]]:
    manifest = {d["doc_id"]: d for d in source.ingested.get("documents", [])}
    excluded = {d["doc_id"]: d.get("excluded_records", 0) for d in source.chunk_manifest.get("documents", [])}
    by_doc: dict[str, list] = {}
    for c in source.chunks:
        by_doc.setdefault(c.doc_id, []).append(c)
    documents = []
    for doc_id in sorted(set(manifest) | set(by_doc)):
        m, cs = manifest.get(doc_id, {}), by_doc.get(doc_id, [])
        first = cs[0] if cs else None
        v = {f: getattr(first, f) for f in _FROM_CHUNK} if first is not None else {f: m.get(f) for f in _FROM_CHUNK}
        if first is None:
            v["is_current"] = {"true": True, "false": False}.get(str(m.get("is_current")))
        provenance = m.get("metadata_provenance")
        documents.append({
            **v, "doc_id": doc_id, "filename": m.get("filename", ""), "status": m.get("status", "unknown"),
            "effective_on": _iso(v["effective_date"]), "effective_date_text": m.get("effective_date"),
            "revision_date": m.get("revision_date"), "superseded_on": _iso(v["superseded_date"]),
            "page_count": m.get("page_count"), "file_sha256": m.get("file_sha256"),
            "content_hash": m.get("content_hash"), "chunk_count": len(cs), "excluded_records": excluded.get(doc_id, 0),
            "missing_metadata": json.dumps(list(m.get("missing_metadata", []))),
            "metadata_provenance": None if provenance is None else json.dumps(provenance, ensure_ascii=False)})
    chunks = [{"chunk_id": c.chunk_id, "doc_id": c.doc_id, "ordinal": c.ordinal, "page_start": c.page_start,
               "page_end": c.page_end, "pages": list(c.pages), "section_path": list(c.section_path),
               "clause_id": c.clause_id, "clause_ids": list(c.clause_ids), "split": c.split,
               "token_count": c.token_count, "confidence": c.confidence, "content_hash": c.content_hash,
               "prev_chunk_id": c.prev_chunk_id, "next_chunk_id": c.next_chunk_id}
              for c in sorted(source.chunks, key=lambda c: (c.doc_id, c.ordinal))]
    load_row = {"ingestion_version": source.ingested.get("pipeline_version"),
                "chunker_version": source.chunk_manifest.get("chunker_version"),
                "ingested_manifest_sha256": source.hashes["ingested"],
                "chunk_manifest_sha256": source.hashes["chunks"], "documents": len(documents), "chunks": len(chunks)}
    return load_row, documents, chunks


def _insert(table: str, fields: tuple[str, ...], casts: dict[str, str] | None = None) -> str:
    casts = casts or {}
    values = ", ".join(f"%({f})s{casts.get(f, '')}" for f in fields)  # constant field names, never caller input
    return f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({values})"


INSERT_DOCUMENT = _insert("documents", ("load_id",) + DOCUMENT_FIELDS,
                          {"missing_metadata": "::jsonb", "metadata_provenance": "::jsonb"})
INSERT_CHUNK = _insert("chunks", CHUNK_FIELDS)


def load(conn, source: RepositoryMetadata) -> dict:
    """Replace the documents and chunks with the repository's, in one transaction. Returns the corpus_loads row."""
    load_row, documents, chunks = rows_of(source)
    with conn.transaction():
        conn.execute("DELETE FROM chunks")
        conn.execute("DELETE FROM documents")
        (load_id,) = conn.execute(
            "INSERT INTO corpus_loads (ingestion_version, chunker_version, ingested_manifest_sha256, "
            "chunk_manifest_sha256, documents, chunks) VALUES (%(ingestion_version)s, %(chunker_version)s, "
            "%(ingested_manifest_sha256)s, %(chunk_manifest_sha256)s, %(documents)s, %(chunks)s) RETURNING load_id",
            load_row).fetchone()
        with conn.cursor() as cur:
            cur.executemany(INSERT_DOCUMENT, [{"load_id": load_id, **d} for d in documents])
            cur.executemany(INSERT_CHUNK, chunks)
        counts = conn.execute("SELECT (SELECT count(*) FROM documents), (SELECT count(*) FROM chunks)").fetchone()
        if counts != (len(documents), len(chunks)):
            raise RuntimeError(f"loaded {counts}, expected {(len(documents), len(chunks))}")
    return {"load_id": load_id, **load_row}
