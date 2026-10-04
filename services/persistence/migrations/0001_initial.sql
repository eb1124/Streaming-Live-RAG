-- AdaptiveRAG phase 9: corpus metadata for the database_query tool.
-- A copy of the repository metadata (data/ingested/_manifest.json, data/chunks/*.jsonl), written by
-- `python -m services.persistence load`; the files stay the source of truth. Runs in the configured schema
-- (search_path); applied once by services/persistence/migrate.py.

CREATE TABLE corpus_loads (
    load_id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loaded_at                timestamptz NOT NULL DEFAULT now(),
    ingestion_version        text,
    chunker_version          text,
    ingested_manifest_sha256 text NOT NULL,
    chunk_manifest_sha256    text NOT NULL,
    documents                integer NOT NULL,
    chunks                   integer NOT NULL
);

CREATE TABLE documents (
    doc_id              text PRIMARY KEY,
    load_id             bigint NOT NULL REFERENCES corpus_loads (load_id),
    filename            text NOT NULL,
    status              text NOT NULL,
    title               text,
    organization        text,
    domain              text,
    document_type       text,
    authority_level     text,
    version             text,
    effective_date      text,          -- as in the chunk metadata (ISO when ingestion could normalize it)
    effective_on        date,          -- effective_date when it is a full ISO date, else NULL
    effective_date_text text,          -- as printed in the document
    revision_date       text,
    issued_date         text,
    superseded_date     text,
    superseded_on       date,          -- superseded_date when it is a full ISO date, else NULL
    is_current          boolean,
    series_id           text,
    source_url          text,
    capture_date        text,
    page_count          integer,
    file_sha256         text,
    content_hash        text,
    chunk_count         integer NOT NULL,
    excluded_records    integer NOT NULL,
    missing_metadata    jsonb NOT NULL DEFAULT '[]'::jsonb,
    metadata_provenance jsonb
);

CREATE TABLE chunks (
    chunk_id      text PRIMARY KEY,
    doc_id        text NOT NULL REFERENCES documents (doc_id) ON DELETE CASCADE,
    ordinal       integer NOT NULL,
    page_start    integer NOT NULL,
    page_end      integer NOT NULL,
    pages         integer[] NOT NULL,
    section_path  text[] NOT NULL,
    clause_id     text,
    clause_ids    text[] NOT NULL,
    split         text NOT NULL,
    token_count   integer NOT NULL,
    confidence    text NOT NULL,
    content_hash  text NOT NULL,
    prev_chunk_id text,
    next_chunk_id text,
    UNIQUE (doc_id, ordinal)
);

CREATE INDEX documents_organization ON documents (organization);
CREATE INDEX documents_series ON documents (series_id, effective_on);
CREATE INDEX documents_domain ON documents (domain);
CREATE INDEX documents_document_type ON documents (document_type);
CREATE INDEX documents_is_current ON documents (is_current);
