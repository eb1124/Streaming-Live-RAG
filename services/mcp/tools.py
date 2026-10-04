"""The three tools' logic (phase 8), independent of the MCP transport: validated input model -> output model.

  document_search   the phase 7 retrieval service (frozen retrieval, or the phase 6 loop) through a SearchBackend;
                    results carry document, page, section, clause, version and score provenance. The optional
                    filters (organization, doc_id, current_only, as_of) are applied to the retrieval's output: they
                    select among the chunks the retrieval returned (its whole reranked pool, MAX_RESULTS) and never
                    widen the search or re-rank. as_of is a metadata filter (MetadataCatalog.applicability), not
                    the frozen temporal resolution of the query text that `temporal` reports.
  metadata_lookup   the repository metadata (MetadataCatalog): documents, filters, one chunk.
  database_query    the DatabaseAdapter contract: NotConfigured, or PostgreSQL (phase 9, services/persistence).

Failures the caller can act on raise ToolFailure (unknown organization / document / chunk, no database); the server
turns them into isError results. An empty result is a result, not a failure.
"""

from __future__ import annotations

from ..contracts import RetrievalRequest
from .backends import SearchBackend
from .catalog import MetadataCatalog
from .contracts import (MAX_RESULTS, AppliedFilters, Correlation, DatabaseQueryInput, DatabaseQueryOutput,
                        DocumentSearchInput, DocumentSearchOutput, MetadataLookupInput, MetadataLookupOutput,
                        Score, SearchResult, TemporalView)
from .database import DatabaseAdapter, DatabaseUnavailable, NotConfigured


class ToolFailure(Exception):
    def __init__(self, code: str, message: str, details: list[dict] | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or []


class Tools:
    def __init__(self, catalog: MetadataCatalog, search: SearchBackend | None,
                 database: DatabaseAdapter | None = None):
        self.catalog, self.search = catalog, search
        self.database = database if database is not None else NotConfigured()

    # ------------------------------------------------------------ shared

    def _organization(self, name: str | None) -> str | None:
        if name is None:
            return None
        org = self.catalog.resolve_organization(name)
        if org is None:
            raise ToolFailure("unknown_organization", f"organization {name!r} is not in the corpus",
                              [{"organizations": self.catalog.organizations,
                                "short_names": sorted(self.catalog.aliases)}])
        return org

    def _document(self, doc_id: str | None) -> str | None:
        if doc_id is not None and doc_id not in self.catalog.doc_ids():
            raise ToolFailure("unknown_document", f"document {doc_id!r} is not in the corpus",
                              [{"doc_ids": self.catalog.doc_ids()}])
        return doc_id

    # ------------------------------------------------------------ document_search

    def document_search(self, inp: DocumentSearchInput, corr: Correlation) -> DocumentSearchOutput:
        if self.search is None:
            raise ToolFailure("backend_unavailable", "no retrieval backend is configured")
        org, doc_id = self._organization(inp.organization), self._document(inp.doc_id)
        req = RetrievalRequest(**corr.model_dump(), query=inp.query, mode=inp.mode, max_rounds=inp.max_rounds,
                               k=MAX_RESULTS)
        resp = self.search.search(req)

        def keep(chunk) -> bool:
            return ((org is None or chunk.organization == org)
                    and (doc_id is None or chunk.doc_id == doc_id)
                    and not (inp.current_only and chunk.is_current is False)
                    and (inp.as_of is None or self.catalog.in_force(chunk, inp.as_of)))

        kept = [e for e in resp.evidence if keep(e.chunk)]
        results = [SearchResult(rank=n, chunk_id=e.chunk.chunk_id, document=self.catalog.document_ref(e.chunk),
                                location=self.catalog.location(e.chunk),
                                score=Score(retrieval_rank=e.rank, value=e.score, retriever=e.retriever,
                                            rerank_score=e.rerank_score),
                                text=e.chunk.text, content_hash=e.chunk.content_hash,
                                as_of_applicability=None if inp.as_of is None
                                else self.catalog.applicability(e.chunk, inp.as_of))
                   for n, e in enumerate(kept[: inp.limit], 1)]
        notes = []
        if not results:
            notes.append(f"no retrieved chunk matched the filters among the {len(resp.evidence)} candidates "
                         "(filters select among the retrieval's results; they do not widen the search)"
                         if resp.evidence else "the retrieval returned no candidates")
        if inp.as_of is not None:
            undetermined = sorted({x.document.doc_id for x in results if x.as_of_applicability == "undetermined"})
            if undetermined:
                notes.append(f"as_of {inp.as_of}: applicability could not be established from metadata for "
                             f"{len(undetermined)} returned document(s) without a full effective date; they are kept "
                             f"unverified (as_of_applicability = undetermined): {', '.join(undetermined)}")
        r = resp.resolution
        return DocumentSearchOutput(
            correlation=corr, query=inp.query, mode=inp.mode, backend=self.search.name,
            filters=AppliedFilters(organization=org, doc_id=doc_id, current_only=inp.current_only, as_of=inp.as_of),
            candidates=len(resp.evidence), filtered_out=len(resp.evidence) - len(kept), results=results,
            temporal=TemporalView(kind=r.kind, trigger=r.trigger, dates=[d.text for d in r.dates],
                                  selected=r.selected, dropped=len(r.dropped), flags=r.flags),
            rounds=resp.trace.iterations if resp.trace is not None else None, notes=notes)

    # ------------------------------------------------------------ metadata_lookup

    def metadata_lookup(self, inp: MetadataLookupInput, corr: Correlation) -> MetadataLookupOutput:
        cat = self.catalog
        if inp.chunk_id is not None:
            chunk = cat.by_id.get(inp.chunk_id)
            if chunk is None:
                raise ToolFailure("not_found", f"chunk {inp.chunk_id!r} is not a retrievable chunk of the corpus")
            return MetadataLookupOutput(correlation=corr, organization=None,
                                        documents=[cat.document(chunk.doc_id, inp.include_provenance)],
                                        chunk=cat.chunk_metadata(chunk, inp.include_text), corpus=cat.corpus_info())
        org, doc_id = self._organization(inp.organization), self._document(inp.doc_id)
        docs = []
        for d in (cat.document(i, inp.include_provenance) for i in cat.doc_ids()):
            if ((doc_id is None or d.doc_id == doc_id) and (org is None or d.organization == org)
                    and (inp.series_id is None or d.series_id == inp.series_id)
                    and (inp.domain is None or d.domain == inp.domain)
                    and (inp.document_type is None or d.document_type == inp.document_type)
                    and not (inp.current_only and d.is_current is False)):
                docs.append(d)
        notes = [] if docs else ["no document matches every filter"]
        return MetadataLookupOutput(correlation=corr, organization=org, documents=docs, corpus=cat.corpus_info(),
                                    notes=notes)

    # ------------------------------------------------------------ database_query

    def database_query(self, inp: DatabaseQueryInput, corr: Correlation) -> DatabaseQueryOutput:
        try:
            return self.database.execute(inp, corr)
        except DatabaseUnavailable as e:
            raise ToolFailure(e.code, str(e), [{"adapter": self.database.name}]) from e
