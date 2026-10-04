"""PostgreSQL persistence for database_query (phase 9).

Offline (always run): configuration, the named queries' constant SQL and bound parameters, the adapter's connection
handling, structured responses, truncation, empty results, every failure path (unreachable, schema not migrated, no
corpus loaded, statement failure), invalid parameters, the migration files and checksum checks, the load rows, the
MCP wiring with and without DATABASE_URL, and isolation from the frozen core. A fake connection stands in for
psycopg; one test connects to a closed local port.

Live (marker `postgres`): skipped unless ADAPTIVERAG_TEST_DATABASE_URL names a PostgreSQL database. Each run works in
its own new schema (adaptiverag_test_<random>) and drops it afterwards; nothing else in that database is touched.
"""

import json
import os
import uuid
from pathlib import Path

import anyio
import pytest
from mcp import Client

psycopg = pytest.importorskip("psycopg")

from services.mcp.catalog import MetadataCatalog  # noqa: E402
from services.mcp.contracts import QUERIES  # noqa: E402
from services.mcp.postgres import PostgresDatabase  # noqa: E402
from services.mcp.server import build_server  # noqa: E402
from services.mcp.tools import Tools  # noqa: E402
from services.persistence import migrate as M  # noqa: E402
from services.persistence.config import DatabaseConfigError, DatabaseSettings  # noqa: E402
from services.persistence.connection import connect  # noqa: E402
from services.persistence.load import RepositoryMetadata, load, read_repository, rows_of  # noqa: E402
from services.persistence.queries import NAMED_QUERIES, bind  # noqa: E402
from tests.test_mcp import ALIASES, CHUNK_MANIFEST, CHUNKS, MANIFEST  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
URL = "postgresql://user:secret@db.invalid:5432/adaptiverag"
UCONN_SERIES = "uconn-travel-entertainment-procedures"
FEB = "travel-and-entertainment-procedures-final-ccccf9"
JUL = "2026-07-01-travel-and-entertainment-procedures-ca903b"


# ---------------------------------------------------------------- a fake psycopg connection


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    """Answers the corpus check with `loaded` and the query with `rows`; or raises `fail` on the query."""

    def __init__(self, rows=(), loaded=True, fail=None):
        self.rows, self.loaded, self.fail = list(rows), loaded, fail
        self.executed, self.read_only, self.closed, self.transactions = [], False, False, 0

    def transaction(self):
        conn = self

        class Tx:
            def __enter__(self):
                conn.transactions += 1

            def __exit__(self, *exc):
                return False
        return Tx()

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "corpus_loads" in sql:
            return FakeCursor([(self.loaded,)])
        if self.fail is not None:
            raise self.fail
        return FakeCursor(self.rows)

    def close(self):
        self.closed = True


class Connector:
    def __init__(self, conn=None, fail=None):
        self.conn, self.fail, self.calls = conn, fail, []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.fail is not None:
            raise self.fail
        return self.conn


def adapter(conn=None, fail=None):
    connector = Connector(conn, fail)
    return connector, PostgresDatabase(DatabaseSettings(url=URL, statement_timeout_ms=1234), connector)


def server_with(database):
    catalog = MetadataCatalog(CHUNKS, MANIFEST, CHUNK_MANIFEST, ALIASES)
    return build_server(Tools(catalog, None, database))


def call(server, arguments):
    async def go():
        async with Client(server) as client:
            return await client.call_tool("database_query", arguments)
    return anyio.run(go)


def ok(server, arguments):
    r = call(server, arguments)
    assert not r.is_error, r.structured_content
    return r.structured_content


def error(server, arguments):
    r = call(server, arguments)
    assert r.is_error
    return r.structured_content["error"]


# ---------------------------------------------------------------- configuration and wiring


def test_settings_from_the_environment(monkeypatch):
    for var in ("DATABASE_URL", "DATABASE_SCHEMA", "DATABASE_CONNECT_TIMEOUT_S", "DATABASE_STATEMENT_TIMEOUT_MS"):
        monkeypatch.delenv(var, raising=False)
    assert DatabaseSettings.from_env() is None
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("DATABASE_STATEMENT_TIMEOUT_MS", "250")
    s = DatabaseSettings.from_env()
    assert (s.url, s.schema, s.connect_timeout_s, s.statement_timeout_ms) == (URL, "adaptiverag", 5, 250)
    assert s.redacted_url() == "postgresql://user:***@db.invalid:5432/adaptiverag"
    for bad in ("Public", "a;drop", "x y", ""):
        with pytest.raises(DatabaseConfigError):
            DatabaseSettings(url=URL, schema=bad)


def test_mcp_process_uses_postgres_only_with_database_url(monkeypatch):
    from services.mcp.__main__ import build_database

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert build_database() is None
    monkeypatch.setenv("DATABASE_URL", URL)
    db = build_database()
    assert isinstance(db, PostgresDatabase) and db.settings.url == URL  # nothing connects until a query runs


def test_without_a_database_the_phase_8_behaviour_is_unchanged():
    e = error(server_with(None), {"query_name": "documents", "session_id": "s-9"})
    assert e["code"] == "database_not_configured" and e["details"] == [{"adapter": "none"}]
    assert "DATABASE_URL" in e["message"] and e["correlation"]["session_id"] == "s-9"


# ---------------------------------------------------------------- named queries


def test_one_constant_statement_per_contract_query():
    assert {n: set(q.parameters) for n, q in NAMED_QUERIES.items()} == {n: set(p) for n, p in QUERIES.items()}
    for name, q in NAMED_QUERIES.items():
        for p in QUERIES[name]:
            assert f"%({p})s" in q.sql
        assert "%(fetch)s" in q.sql and "ORDER BY" in q.sql and ";" not in q.sql
        assert q.sql.split("FROM")[0].count(",") == len(q.columns) - 1  # the SELECT list is the published columns


def test_caller_values_are_bound_parameters_never_sql_text():
    hostile = "x'; DROP TABLE documents; --"
    sql_a, params_a = bind("documents", {"organization": hostile}, 50)
    sql_b, params_b = bind("documents", {"organization": "Yale University"}, 50)
    assert sql_a == sql_b and hostile not in sql_a.sql
    assert params_a == {"organization": hostile, "domain": None, "document_type": None, "is_current": None,
                        "fetch": 51}
    _, p = bind("chunks_by_document", {"doc_id": "d"}, 3)
    assert p == {"doc_id": "d", "fetch": 4}
    with pytest.raises(ValueError):
        bind("documents", {"owner": "x"}, 5)  # the repository refuses what the contract would have refused


# ---------------------------------------------------------------- the adapter


def test_adapter_runs_the_fixed_statement_read_only_and_closes_the_connection():
    conn = FakeConnection(rows=[("proc-feb", UCONN_SERIES, "T", "Test University", "2026-02-01", "2026-07-01",
                                 False, "Procedures-FEB.pdf")])
    connector, db = adapter(conn)
    out = ok(server_with(db), {"query_name": "document_versions", "parameters": {"series_id": UCONN_SERIES},
                               "request_id": "r-9", "job_id": "j-9"})
    (url, kwargs), = connector.calls
    assert url == URL and kwargs["connect_timeout"] == 5
    assert "-c search_path=adaptiverag" in kwargs["options"] and "-c statement_timeout=1234" in kwargs["options"]
    assert conn.read_only is True and conn.closed is True and conn.transactions == 1
    query = NAMED_QUERIES["document_versions"]
    assert conn.executed[-1] == (query.sql, {"series_id": UCONN_SERIES, "fetch": 51})
    assert out["source"] == "postgresql" and out["columns"] == list(query.columns)
    assert out["rows"] == [dict(zip(query.columns, conn.rows[0]))]
    assert (out["row_count"], out["truncated"]) == (1, False)
    assert out["correlation"] == {"request_id": "r-9", "job_id": "j-9", "session_id": None}


def test_truncation_fetches_one_row_more_than_the_limit():
    rows = [(f"c{i}", "d", i, 1, 1, [1], [], None, [], "structural", 10, "high", "h") for i in range(4)]
    conn = FakeConnection(rows=rows)
    _, db = adapter(conn)
    out = ok(server_with(db), {"query_name": "chunks_by_document", "parameters": {"doc_id": "d"}, "limit": 3})
    assert conn.executed[-1][1]["fetch"] == 4
    assert [r["chunk_id"] for r in out["rows"]] == ["c0", "c1", "c2"]
    assert (out["row_count"], out["truncated"]) == (3, True)


def test_empty_result_is_a_result():
    _, db = adapter(FakeConnection(rows=[]))
    out = ok(server_with(db), {"query_name": "documents", "parameters": {"organization": "Nowhere University"}})
    assert (out["rows"], out["row_count"], out["truncated"]) == ([], 0, False)
    assert out["columns"] == list(NAMED_QUERIES["documents"].columns)


@pytest.mark.parametrize("make, text", [
    (lambda: (None, psycopg.OperationalError("connection refused")), "cannot connect to"),
    (lambda: (FakeConnection(fail=psycopg.errors.UndefinedTable("relation \"documents\" does not exist")), None),
     "is not initialized: run python -m services.persistence migrate"),
    (lambda: (FakeConnection(loaded=False), None), "no corpus is loaded"),
    (lambda: (FakeConnection(fail=psycopg.errors.QueryCanceled("canceling statement due to statement timeout")),
              None), "documents failed: canceling statement due to statement timeout"),
])
def test_a_configured_database_that_cannot_answer_is_database_unavailable(make, text):
    conn, fail = make()
    connector, db = adapter(conn, fail)
    e = error(server_with(db), {"query_name": "documents", "request_id": "r-fail"})
    assert e["code"] == "database_unavailable" and text in e["message"]
    assert e["details"] == [{"adapter": "postgresql"}] and e["correlation"]["request_id"] == "r-fail"
    assert "secret" not in json.dumps(e)  # the password never reaches the caller
    if conn is not None:
        assert conn.closed


def test_a_closed_local_port_is_database_unavailable():
    db = PostgresDatabase(DatabaseSettings(url="postgresql://u:pw@127.0.0.1:1/x", connect_timeout_s=2))
    e = error(server_with(db), {"query_name": "documents"})
    assert e["code"] == "database_unavailable" and e["message"].startswith("cannot connect to postgresql://u:***@")


@pytest.mark.parametrize("arguments", [
    {"query_name": "documents", "parameters": {"is_current": "yes"}},
    {"query_name": "documents", "parameters": {"is_current": 1}},
    {"query_name": "documents", "parameters": {"organization": 5}},
    {"query_name": "document_versions", "parameters": {"series_id": True}},
    {"query_name": "chunks_by_document", "parameters": {"doc_id": None}},
    {"query_name": "chunks_by_document", "parameters": {"doc_id": "d", "ordinal": 1}},
    {"query_name": "documents", "sql": "SELECT 1"},
    {"query_name": "documents; DROP TABLE documents"},
    {"query_name": "documents", "limit": 0},
])
def test_invalid_arguments_never_reach_the_database(arguments):
    connector, db = adapter(FakeConnection())
    e = error(server_with(db), arguments)
    assert e["code"] == "invalid_arguments" and connector.calls == []


# ---------------------------------------------------------------- migrations and load rows


def test_migration_files_are_ordered_and_checksummed(tmp_path):
    files = M.migrations()
    assert [(m.version, m.name) for m in files] == [(1, "initial")]
    assert len(files[0].checksum) == 64 and "CREATE TABLE documents" in files[0].sql
    (tmp_path / "0002_later.sql").write_text("SELECT 1")
    with pytest.raises(M.MigrationError, match="without gaps"):
        M.migrations(tmp_path)
    (tmp_path / "bad name.sql").write_text("SELECT 1")
    with pytest.raises(M.MigrationError, match="does not match"):
        M.migrations(tmp_path)


def test_an_applied_migration_that_changed_or_vanished_is_refused():
    files = M.migrations()

    class Applied(FakeConnection):
        def __init__(self, rows):
            super().__init__()
            self.applied_rows = rows

        def execute(self, sql, params=None):
            return FakeCursor(self.applied_rows)

    assert M.check(Applied([]), files) == files
    assert M.check(Applied([(1, "initial", files[0].checksum)]), files) == []
    with pytest.raises(M.MigrationError, match="changed after it was applied"):
        M.check(Applied([(1, "initial", "0" * 64)]), files)
    with pytest.raises(M.MigrationError, match="file is missing"):
        M.check(Applied([(1, "initial", files[0].checksum), (2, "gone", "x")]), files)


def synthetic_source():
    return RepositoryMetadata(MANIFEST, list(CHUNKS), CHUNK_MANIFEST, {"ingested": "a" * 64, "chunks": "b" * 64})


def test_load_rows_are_the_repository_values():
    load_row, docs, chunks = rows_of(synthetic_source())
    assert load_row == {"ingestion_version": "ingest-test", "chunker_version": "chunk-test",
                        "ingested_manifest_sha256": "a" * 64, "chunk_manifest_sha256": "b" * 64,
                        "documents": 4, "chunks": 5}
    by = {d["doc_id"]: d for d in docs}
    feb = by["proc-feb"]
    assert (feb["effective_date"], str(feb["effective_on"]), feb["superseded_date"], str(feb["superseded_on"])) == (
        "2026-02-01", "2026-02-01", "2026-07-01", "2026-07-01")
    assert feb["is_current"] is False and feb["series_id"] and feb["filename"] == "Procedures-FEB.pdf"
    assert json.loads(feb["missing_metadata"]) == ["version"] and json.loads(feb["metadata_provenance"])
    assert by["ru"]["effective_on"] is None and by["ru"]["chunk_count"] == 2 and by["ru"]["excluded_records"] == 1
    assert [c["chunk_id"] for c in chunks] == sorted(c.chunk_id for c in CHUNKS)  # (doc_id, ordinal) order
    assert rows_of(synthetic_source()) == (load_row, docs, chunks)


DOC_FIELDS = ("filename", "status", "title", "organization", "domain", "document_type", "authority_level", "version",
              "effective_date", "effective_date_text", "revision_date", "issued_date", "superseded_date", "is_current",
              "series_id", "source_url", "capture_date", "page_count", "file_sha256", "content_hash")


def assert_rows_equal_catalog(source, catalog):
    _, docs, _ = rows_of(source)
    assert [d["doc_id"] for d in docs] == catalog.doc_ids()
    for d in docs:
        m = catalog.document(d["doc_id"], include_provenance=True)
        assert {f: d[f] for f in DOC_FIELDS} == {f: getattr(m, f) for f in DOC_FIELDS}, d["doc_id"]
        assert (d["chunk_count"], d["excluded_records"]) == (m.chunks, m.excluded_records)
        assert json.loads(d["missing_metadata"]) == m.missing_metadata
        assert (None if d["metadata_provenance"] is None else json.loads(d["metadata_provenance"])) == m.provenance


def test_load_rows_equal_what_metadata_lookup_reports():
    assert_rows_equal_catalog(synthetic_source(), MetadataCatalog(CHUNKS, MANIFEST, CHUNK_MANIFEST, ALIASES))


@pytest.mark.skipif(not (ROOT / "data" / "ingested" / "_manifest.json").exists(), reason="no data/")
def test_real_load_rows_equal_what_metadata_lookup_reports():
    assert_rows_equal_catalog(read_repository(), MetadataCatalog.load())


# ---------------------------------------------------------------- isolation


FROZEN = ["retrieval", "generation", "temporal", "chunking", "ingestion", "adaptive", "config"]


def test_layering_psycopg_only_in_persistence_and_persistence_independent_of_mcp():
    """MCP tool -> DatabaseAdapter (services/mcp/postgres.py) -> repository (services/persistence) -> PostgreSQL.
    psycopg only in services/persistence; persistence knows nothing of MCP; the frozen core and the phase 7
    services do not use persistence."""
    for path in ROOT.glob("**/*.py"):
        rel = path.relative_to(ROOT)
        if rel.parts[0] in (".venv", "tests"):
            continue
        source = path.read_text(encoding="utf-8")
        if "persistence" in rel.parts:
            assert "services.mcp" not in source and "from mcp" not in source and "import mcp" not in source, rel
            continue
        assert "import psycopg" not in source and "from psycopg" not in source, rel
        if rel.parts[0] in FROZEN or (rel.parts[0] == "services" and "mcp" not in rel.parts):
            assert "services.persistence" not in source and "..persistence" not in source, rel


# ---------------------------------------------------------------- live PostgreSQL


LIVE_URL = os.environ.get("ADAPTIVERAG_TEST_DATABASE_URL")
live = pytest.mark.skipif(not LIVE_URL, reason="PostgreSQL tests disabled (set ADAPTIVERAG_TEST_DATABASE_URL)")
needs_data = pytest.mark.skipif(not (ROOT / "data" / "ingested" / "_manifest.json").exists(), reason="no data/")


@pytest.fixture
def pg():
    settings = DatabaseSettings(url=LIVE_URL, schema=f"adaptiverag_test_{uuid.uuid4().hex[:12]}")
    yield settings
    with connect(settings) as conn:
        M.drop_schema(conn, settings.schema)


def migrated(settings):
    with connect(settings) as conn:
        return M.apply(conn, settings.schema)


def loaded(settings):
    migrated(settings)
    with connect(settings) as conn:
        return load(conn, read_repository())


@pytest.mark.postgres
@live
def test_live_migrations_create_the_schema_once(pg):
    assert [m.name for m in migrated(pg)] == ["initial"]
    assert migrated(pg) == []  # idempotent
    with connect(pg) as conn:
        (checksum,) = conn.execute("SELECT checksum FROM schema_migrations WHERE version = 1").fetchone()
        tables = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s", (pg.schema,))}
        indexes = {r[0] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = %s", (pg.schema,))}
        (timeout,) = conn.execute("SHOW statement_timeout").fetchone()
        conn.execute("UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 1")
        conn.commit()
    assert checksum == M.migrations()[0].checksum
    assert tables == {"schema_migrations", "corpus_loads", "documents", "chunks"}
    assert {"documents_organization", "documents_series", "chunks_doc_id_ordinal_key"} <= indexes
    assert timeout == "5s"
    with pytest.raises(M.MigrationError, match="changed after it was applied"):
        migrated(pg)


@pytest.mark.postgres
@live
@needs_data
def test_live_load_copies_the_repository_metadata_and_reloads_identically(pg):
    first = loaded(pg)
    catalog = MetadataCatalog.load()
    assert (first["documents"], first["chunks"]) == (len(catalog.doc_ids()), len(catalog.chunks)) == (12, 466)
    with connect(pg) as conn:
        def snapshot():
            return (conn.execute("SELECT doc_id, title, organization, effective_date, effective_on, is_current, "
                                 "series_id, filename, chunk_count FROM documents ORDER BY doc_id").fetchall(),
                    conn.execute("SELECT chunk_id, doc_id, ordinal, pages, content_hash FROM chunks "
                                 "ORDER BY chunk_id").fetchall())
        before = snapshot()
        load(conn, read_repository())
        assert snapshot() == before
        assert conn.execute("SELECT count(*) FROM corpus_loads").fetchone() == (2,)
    for doc_id, title, org, eff, _, current, series, filename, n in before[0]:
        d = catalog.document(doc_id)
        assert (title, org, eff, current, series, filename, n) == (
            d.title, d.organization, d.effective_date, d.is_current, d.series_id, d.filename, d.chunks)


@pytest.mark.postgres
@live
@needs_data
def test_live_database_query_through_mcp(pg):
    loaded(pg)
    catalog = MetadataCatalog.load()
    server = server_with(PostgresDatabase(pg))
    every = ok(server, {"query_name": "documents"})
    assert [r["doc_id"] for r in every["rows"]] == catalog.doc_ids() and every["row_count"] == 12
    uconn = ok(server, {"query_name": "documents", "parameters": {"organization": "University of Connecticut"}})
    assert {r["doc_id"] for r in uconn["rows"]} == {FEB, JUL, "travel-and-entertainment-policy-university-5f7c66"}
    old = ok(server, {"query_name": "documents", "parameters": {"is_current": False}})
    assert [r["doc_id"] for r in old["rows"]] == [FEB]
    versions = ok(server, {"query_name": "document_versions", "parameters": {"series_id": UCONN_SERIES}})
    assert [(r["doc_id"], r["effective_date"], r["is_current"]) for r in versions["rows"]] == [
        (FEB, "2026-02-01", False), (JUL, "2026-07-01", True)]
    chunks = ok(server, {"query_name": "chunks_by_document", "parameters": {"doc_id": FEB}, "limit": 500})
    assert [r["chunk_id"] for r in chunks["rows"]] == [c.chunk_id for c in catalog.by_doc[FEB]]
    page = ok(server, {"query_name": "chunks_by_document", "parameters": {"doc_id": FEB}, "limit": 5})
    assert page["row_count"] == 5 and page["truncated"] is True and page["rows"] == chunks["rows"][:5]
    assert ok(server, {"query_name": "document_versions", "parameters": {"series_id": "none"}})["rows"] == []
    hostile = ok(server, {"query_name": "documents", "parameters": {"organization": "x' OR '1'='1"}})
    assert hostile["rows"] == [] and ok(server, {"query_name": "documents"})["row_count"] == 12


@pytest.mark.postgres
@live
def test_live_unmigrated_and_unloaded_schemas_are_reported(pg):
    server = server_with(PostgresDatabase(pg))
    e = error(server, {"query_name": "documents"})
    assert e["code"] == "database_unavailable" and "is not initialized" in e["message"]
    migrated(pg)
    e = error(server, {"query_name": "documents"})
    assert e["code"] == "database_unavailable" and "no corpus is loaded" in e["message"]
