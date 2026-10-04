"""The named metadata queries (phase 9), one fixed SQL statement each.

The names and parameters are those of the MCP database_query contract (services/mcp/contracts.py QUERIES); the MCP
adapter (services/mcp/postgres.py) checks that the two agree. Every statement is a constant: caller values only
ever travel as bound parameters (%(name)s), an absent optional filter is NULL and matches everything, and every
ORDER BY ends on a primary key, so the same database state always gives the same rows in the same order (text keys
sort with COLLATE "C": byte order, independent of the database locale). One row more than the limit is fetched to
report truncation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NamedQuery:
    name: str
    parameters: tuple[str, ...]
    columns: tuple[str, ...]
    sql: str


_DOCUMENT_COLUMNS = ("doc_id", "title", "organization", "domain", "document_type", "authority_level", "version",
                     "effective_date", "superseded_date", "is_current", "series_id", "filename", "page_count",
                     "chunk_count")

NAMED_QUERIES = {q.name: q for q in [
    NamedQuery("documents", ("organization", "domain", "document_type", "is_current"), _DOCUMENT_COLUMNS, f"""
        SELECT {", ".join(_DOCUMENT_COLUMNS)}
          FROM documents
         WHERE (%(organization)s::text IS NULL OR organization = %(organization)s::text)
           AND (%(domain)s::text IS NULL OR domain = %(domain)s::text)
           AND (%(document_type)s::text IS NULL OR document_type = %(document_type)s::text)
           AND (%(is_current)s::boolean IS NULL OR is_current = %(is_current)s::boolean)
         ORDER BY doc_id COLLATE "C"
         LIMIT %(fetch)s"""),
    NamedQuery("document_versions", ("series_id",),
               ("doc_id", "series_id", "title", "organization", "effective_date", "superseded_date", "is_current",
                "filename"), """
        SELECT doc_id, series_id, title, organization, effective_date, superseded_date, is_current, filename
          FROM documents
         WHERE series_id = %(series_id)s::text
         ORDER BY effective_on NULLS LAST, doc_id COLLATE "C"
         LIMIT %(fetch)s"""),
    NamedQuery("chunks_by_document", ("doc_id",),
               ("chunk_id", "doc_id", "ordinal", "page_start", "page_end", "pages", "section_path", "clause_id",
                "clause_ids", "split", "token_count", "confidence", "content_hash"), """
        SELECT chunk_id, doc_id, ordinal, page_start, page_end, pages, section_path, clause_id, clause_ids, split,
               token_count, confidence, content_hash
          FROM chunks
         WHERE doc_id = %(doc_id)s::text
         ORDER BY ordinal, chunk_id COLLATE "C"
         LIMIT %(fetch)s"""),
]}


def bind(name: str, parameters: dict, limit: int) -> tuple[NamedQuery, dict]:
    """The statement and its parameters: each of the query's parameters (None when absent) + fetch = limit + 1.
    Callers validate first (the MCP contract does); an unknown name or parameter is a programming error."""
    q = NAMED_QUERIES[name]
    unknown = set(parameters) - set(q.parameters)
    if unknown:
        raise ValueError(f"unknown parameters for {name}: {sorted(unknown)}")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    params = {p: parameters.get(p) for p in q.parameters}
    params["fetch"] = limit + 1
    return q, params
