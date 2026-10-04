"""MCP tool contracts (phase 8): the input and output of every tool, as Pydantic models.

The tool schemas published by tools/list are these models' JSON schemas: inputs reject unknown fields
(additionalProperties: false), outputs are validated before they leave the server and are checked again by MCP
clients against the published outputSchema. Errors are not outputs: they are isError tool results carrying a
ToolError (see services/mcp/server.py).

Correlation (as in phase 7, services/correlation.py): every tool accepts optional request_id / job_id / session_id;
the server makes a request_id when none is given, binds the ids while the tool runs (so a remote retrieval call
carries them to the retrieval service) and echoes them in the output.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1
ID_PATTERN = r"^[A-Za-z0-9_.:-]+$"
MAX_RESULTS = 20  # = retrieval.rerank.CANDIDATE_POOL: the frozen retrieval returns at most this many chunks


def _id(description: str):
    return Field(default=None, min_length=1, max_length=128, pattern=ID_PATTERN, description=description)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Input(_Model):
    request_id: str | None = _id("Correlation id of this call; made by the server when omitted.")
    job_id: str | None = _id("Phase 4 job id, when the call belongs to a job.")
    session_id: str | None = _id("Conversation (phase 3 session) id, when the call belongs to one.")


class Correlation(_Model):
    request_id: str
    job_id: str | None = None
    session_id: str | None = None


# ---------------------------------------------------------------- document_search


class DocumentSearchInput(_Input):
    query: str = Field(min_length=1, max_length=4000, description="The question or search text.")
    limit: int = Field(default=5, ge=1, le=MAX_RESULTS, description="Maximum number of results.")
    mode: Literal["single", "iterative"] = Field(
        default="single", description="single: the frozen retrieval; iterative: the phase 6 bounded loop.")
    max_rounds: int = Field(default=3, ge=1, le=10, description="Round budget of the iterative mode.")
    organization: str | None = Field(
        default=None, min_length=1, max_length=200,
        description="Keep only chunks of this organization (full corpus name or a configured short name).")
    doc_id: str | None = Field(default=None, min_length=1, max_length=200, description="Keep only this document.")
    current_only: bool = Field(default=False, description="Drop chunks of documents marked not current.")
    as_of: date | None = Field(
        default=None, description="ISO date: of a multi-version series keep only the version in force that day.")

    @model_validator(mode="after")
    def _one_version_filter(self):
        if self.current_only and self.as_of is not None:
            raise ValueError("current_only and as_of are mutually exclusive")
        return self


class DocumentRef(_Model):
    """The document a chunk belongs to: chunk metadata (copied from ingestion) and the ingestion manifest."""

    doc_id: str
    title: str | None
    filename: str | None  # the corpus PDF
    organization: str | None
    domain: str | None
    document_type: str | None
    authority_level: str | None
    version: str | None
    effective_date: str | None
    superseded_date: str | None
    is_current: bool | None
    series_id: str | None
    source_url: str | None
    page_count: int | None


class Location(_Model):
    page_start: int
    page_end: int
    pages: list[int]
    section_path: list[str]
    clause_id: str | None
    clause_ids: list[str]
    source_block_ids: list[str]


class Score(_Model):
    retrieval_rank: int  # rank in the frozen retrieval's output, before the tool's filters
    value: float | None  # the retriever's score (cross-encoder logit when reranked, else the RRF score)
    retriever: str  # e.g. hybrid-rrf-temporal-rerank
    rerank_score: float | None


class SearchResult(_Model):
    rank: int  # 1-based, after filters
    chunk_id: str
    document: DocumentRef
    location: Location
    score: Score
    text: str  # the chunk's own text, verbatim
    content_hash: str
    # Only with as_of: "in_force", or "undetermined" when the document has no usable effective date (kept, unverified)
    as_of_applicability: Literal["in_force", "undetermined"] | None = None


class TemporalView(_Model):
    """temporal.resolve.Resolution of the query text by the frozen retrieval (dates and words like "currently" in the
    question). It does not reflect the as_of argument, which is a separate metadata filter (see AppliedFilters)."""

    kind: str  # neutral | point_in_time | compare | current
    trigger: str
    dates: list[str]
    selected: dict[str, list[str]]  # series_id -> doc_ids the question selected
    dropped: int  # candidates of unselected versions removed by the resolution
    flags: list[str]


class AppliedFilters(_Model):
    organization: str | None  # resolved full name
    doc_id: str | None
    current_only: bool
    as_of: date | None


class DocumentSearchOutput(_Model):
    schema_version: int = SCHEMA_VERSION
    correlation: Correlation
    query: str
    mode: Literal["single", "iterative"]
    backend: str  # local | remote <url>
    filters: AppliedFilters
    candidates: int  # chunks the retrieval returned before the tool's filters
    filtered_out: int
    results: list[SearchResult]
    temporal: TemporalView
    rounds: int | None = None  # phase 6 rounds (iterative mode)
    notes: list[str] = []


# ---------------------------------------------------------------- metadata_lookup


class MetadataLookupInput(_Input):
    chunk_id: str | None = Field(default=None, min_length=1, max_length=300,
                                 description="Look up one chunk (and its document). Excludes the filters below.")
    doc_id: str | None = Field(default=None, min_length=1, max_length=200)
    organization: str | None = Field(default=None, min_length=1, max_length=200,
                                     description="Full corpus name or a configured short name.")
    series_id: str | None = Field(default=None, min_length=1, max_length=200)
    domain: str | None = Field(default=None, min_length=1, max_length=100)
    document_type: str | None = Field(default=None, min_length=1, max_length=100)
    current_only: bool = False
    include_provenance: bool = Field(default=False, description="Add the ingestion metadata provenance per field.")
    include_text: bool = Field(default=False, description="Chunk lookup: add the chunk text.")

    @model_validator(mode="after")
    def _chunk_or_filters(self):
        filters = [self.doc_id, self.organization, self.series_id, self.domain, self.document_type]
        if self.chunk_id is not None and (any(f is not None for f in filters) or self.current_only):
            raise ValueError("chunk_id cannot be combined with document filters")
        return self


class DocumentMetadata(_Model):
    doc_id: str
    filename: str
    status: str
    title: str | None
    organization: str | None
    domain: str | None
    document_type: str | None
    authority_level: str | None
    version: str | None
    effective_date: str | None  # normalized (as in the chunks) when ingestion could, else raw
    effective_date_text: str | None  # as printed in the document
    revision_date: str | None
    issued_date: str | None
    superseded_date: str | None
    is_current: bool | None
    series_id: str | None
    series_versions: list[str]  # doc_ids of the series, oldest effective date first ([] without a series)
    source_url: str | None
    capture_date: str | None
    page_count: int | None
    file_sha256: str | None
    content_hash: str | None
    chunks: int  # retrievable chunks
    excluded_records: int
    missing_metadata: list[str]
    provenance: dict[str, Any] | None = None


class ChunkMetadata(_Model):
    chunk_id: str
    doc_id: str
    ordinal: int
    location: Location
    prev_chunk_id: str | None
    next_chunk_id: str | None
    split: str
    token_count: int
    confidence: str
    content_hash: str
    text: str | None = None


class CorpusInfo(_Model):
    documents: int
    chunks: int
    organizations: list[str]
    ingestion_version: str | None
    chunker_version: str | None


class MetadataLookupOutput(_Model):
    schema_version: int = SCHEMA_VERSION
    correlation: Correlation
    organization: str | None  # the resolved organization filter
    documents: list[DocumentMetadata]
    chunk: ChunkMetadata | None = None
    corpus: CorpusInfo
    notes: list[str] = []


# ---------------------------------------------------------------- database_query (contract only)


QueryName = Literal["documents", "document_versions", "chunks_by_document"]
QUERIES: dict[str, dict[str, bool]] = {  # named query -> parameter -> required
    "documents": {"organization": False, "domain": False, "document_type": False, "is_current": False},
    "document_versions": {"series_id": True},
    "chunks_by_document": {"doc_id": True},
}
PARAMETER_TYPES: dict[str, type] = {  # parameter -> the type its value must have (None = absent filter)
    "organization": str, "domain": str, "document_type": str, "is_current": bool, "series_id": str, "doc_id": str,
}


class DatabaseQueryInput(_Input):
    """A named, parameterized query. No SQL text is accepted: the adapter maps names to its own statements."""

    query_name: QueryName
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    limit: int = Field(default=50, ge=1, le=500)

    @model_validator(mode="after")
    def _parameters_of_the_query(self):
        spec = QUERIES[self.query_name]
        unknown = sorted(set(self.parameters) - set(spec))
        missing = sorted(p for p, required in spec.items() if required and self.parameters.get(p) is None)
        if unknown:
            raise ValueError(f"unknown parameters for {self.query_name}: {unknown}; allowed: {sorted(spec)}")
        if missing:
            raise ValueError(f"missing parameters for {self.query_name}: {missing}")
        mistyped = sorted(p for p, v in self.parameters.items()
                          if v is not None and type(v) is not PARAMETER_TYPES[p])
        if mistyped:
            raise ValueError("parameters of the wrong type: " + ", ".join(
                f"{p} must be {PARAMETER_TYPES[p].__name__}" for p in mistyped))
        return self


class DatabaseQueryOutput(_Model):
    schema_version: int = SCHEMA_VERSION
    correlation: Correlation
    query_name: QueryName
    source: str  # the adapter, e.g. "postgresql"
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool


# ---------------------------------------------------------------- errors


class ToolErrorDetail(_Model):
    code: Literal["invalid_arguments", "unknown_organization", "unknown_document", "not_found",
                  "database_not_configured", "database_unavailable", "backend_unavailable",
                  "internal_error"]
    message: str
    details: list[dict[str, Any]] = []
    correlation: Correlation | None = None
