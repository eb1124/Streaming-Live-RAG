"""Phase 9 evaluation: PostgreSQL behind database_query, against a live database. Deterministic: no language model,
no retrieval, no network beyond the database.

  $env:DATABASE_URL = "postgresql://adaptiverag:<password>@127.0.0.1:5433/adaptiverag"
  python -m evaluation.database.run          # writes results/postgres.json and results/postgres.md

Works in its own schema (adaptiverag_eval), dropped and rebuilt on every run, so the result does not depend on what
the database held before; the schema the services use (DATABASE_SCHEMA) is not touched. Every query goes through the
MCP protocol (mcp.Client in memory -> services.mcp.server -> PostgresDatabase). Expectations come from the
repository metadata (MetadataCatalog over data/), never from the database itself.

Suites:
  schema       migrations apply once, are recorded with their checksum, create the tables and indexes, are
               idempotent, and refuse an applied migration whose checksum changed
  load         12 documents / 466 chunks, equal to the catalog; a second load leaves identical rows
  queries      documents (all, per organization, domain, type, currency), document_versions (every series and an
               unknown one), chunks_by_document (every document): rows and order equal the catalog's;
               documents rows equal metadata_lookup's documents; truncation; a hostile value matches nothing
  failures     no DATABASE_URL, a closed port, an unmigrated schema, an unloaded schema, invalid parameters
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
SCHEMA = "adaptiverag_eval"


class Calls:
    def __init__(self, client):
        self.client = client

    async def __call__(self, tool: str, arguments: dict) -> dict:
        r = await self.client.call_tool(tool, arguments)
        return {"is_error": bool(r.is_error), "out": r.structured_content}


def row(cid: str, problems: list[str], **info) -> dict:
    return {"id": cid, **info, "checks": problems}


def schema_suite(settings) -> list[dict]:
    from services.persistence import migrate as M
    from services.persistence.connection import connect

    rows = []
    with connect(settings) as conn:
        first = M.apply(conn, settings.schema)
    rows.append(row("first-migrate", [] if [m.name for m in first] == ["initial"] else [f"applied {first}"]))
    with connect(settings) as conn:
        second = M.apply(conn, settings.schema)
        recorded = conn.execute("SELECT version, name, checksum FROM schema_migrations").fetchall()
        tables = sorted(r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s",
                                                   (settings.schema,)))
        indexes = sorted(r[0] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = %s",
                                                    (settings.schema,)))
    rows.append(row("idempotent", [] if second == [] else [f"applied again {second}"]))
    files = M.migrations()
    rows.append(row("recorded-checksums", [] if recorded == [(m.version, m.name, m.checksum) for m in files]
                    else [f"recorded {recorded}"]))
    want_tables = ["chunks", "corpus_loads", "documents", "schema_migrations"]
    rows.append(row("tables", [] if tables == want_tables else [f"tables {tables}"], tables=tables))
    want_idx = {"documents_organization", "documents_series", "documents_domain", "documents_document_type",
                "documents_is_current", "chunks_doc_id_ordinal_key"}
    rows.append(row("indexes", [] if want_idx <= set(indexes) else [f"missing {sorted(want_idx - set(indexes))}"],
                    indexes=indexes))
    with connect(settings) as conn:
        conn.execute("UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 1")
        conn.commit()
        try:
            M.apply(conn, settings.schema)
            refused = False
        except M.MigrationError:
            refused = True
            conn.rollback()
        conn.execute("UPDATE schema_migrations SET checksum = %s WHERE version = 1", (files[0].checksum,))
        conn.commit()
    rows.append(row("changed-migration-refused", [] if refused else ["a changed migration was accepted"]))
    return rows


def load_suite(settings, catalog) -> list[dict]:
    from services.persistence.connection import connect
    from services.persistence.load import load, read_repository

    rows = []
    source = read_repository()
    hashes = source.hashes
    with connect(settings) as conn:
        first = load(conn, source)

        def snapshot():
            return (conn.execute("SELECT * FROM documents ORDER BY doc_id").fetchall(),
                    conn.execute("SELECT * FROM chunks ORDER BY chunk_id").fetchall())
        # load_id differs between loads: compare documents without it
        before = snapshot()
        second = load(conn, source)
        after = snapshot()
        loads = conn.execute("SELECT count(*) FROM corpus_loads").fetchone()[0]
    want = (len(catalog.doc_ids()), len(catalog.chunks))
    rows.append(row("counts", [] if (first["documents"], first["chunks"]) == want else [f"loaded {first}"],
                    documents=first["documents"], chunks=first["chunks"]))
    strip = lambda docs: [r[:1] + r[2:] for r in docs]  # noqa: E731  (column 2 is load_id)
    same = strip(before[0]) == strip(after[0]) and before[1] == after[1]
    rows.append(row("reload-identical", [] if same and loads == 2 and second["load_id"] == first["load_id"] + 1
                    else ["second load differs"], corpus_loads=loads))
    rows.append(row("manifest-hashes", [] if (first["ingested_manifest_sha256"], first["chunk_manifest_sha256"]) ==
                    (hashes["ingested"], hashes["chunks"]) else ["hashes"]))
    return rows


async def query_suite(call, catalog) -> list[dict]:
    rows = []
    docs = {d: catalog.document(d) for d in catalog.doc_ids()}
    fields = ("title", "organization", "domain", "document_type", "authority_level", "version", "effective_date",
              "superseded_date", "is_current", "series_id", "filename", "page_count")

    async def ids(args) -> tuple[list[str] | None, dict]:
        r = await call("database_query", args)
        if r["is_error"]:
            return None, r["out"]
        return [x["doc_id"] for x in r["out"]["rows"]], r["out"]

    got, out = await ids({"query_name": "documents"})
    expected = sorted(docs)  # byte order, as COLLATE "C"
    problems = [] if got == expected else [f"documents {got}"]
    if got is not None:
        for x in out["rows"]:
            d = docs[x["doc_id"]]
            bad = [f for f in fields if x[f] != getattr(d, f)] + ([] if x["chunk_count"] == d.chunks else ["chunks"])
            if bad:
                problems.append(f"{x['doc_id']}: {bad}")
    rows.append(row("documents-all", problems, rows=len(got or [])))

    lookup = await call("metadata_lookup", {})
    by_lookup = {d["doc_id"]: d for d in lookup["out"]["documents"]}
    diff = [x["doc_id"] for x in out["rows"] if any(x[f] != by_lookup[x["doc_id"]][f] for f in fields)]
    rows.append(row("documents-equal-metadata_lookup", [] if not diff and set(by_lookup) == set(got or []) else diff))

    for field in ("organization", "domain", "document_type"):
        for value in sorted({getattr(d, field) for d in docs.values() if getattr(d, field)}):
            want = sorted(i for i, d in docs.items() if getattr(d, field) == value)
            got, _ = await ids({"query_name": "documents", "parameters": {field: value}})
            rows.append(row(f"documents {field}={value}", [] if got == want else [f"got {got}"], rows=len(got or [])))
    for value in (True, False):
        want = sorted(i for i, d in docs.items() if d.is_current is value)
        got, _ = await ids({"query_name": "documents", "parameters": {"is_current": value}})
        rows.append(row(f"documents is_current={value}", [] if got == want else [f"got {got}"], rows=len(got or [])))

    series = sorted({d.series_id for d in docs.values() if d.series_id})
    for s in series:
        r = await call("database_query", {"query_name": "document_versions", "parameters": {"series_id": s}})
        got = [(x["doc_id"], x["effective_date"], x["is_current"]) for x in r["out"]["rows"]]
        member = next(i for i, d in docs.items() if d.series_id == s)
        want = [(i, docs[i].effective_date, docs[i].is_current) for i in catalog.series_versions(member)]
        rows.append(row(f"document_versions {s}", [] if got == want else [f"got {got}"], versions=got))
    got, _ = await ids({"query_name": "document_versions", "parameters": {"series_id": "no-such-series"}})
    rows.append(row("document_versions unknown", [] if got == [] else [f"got {got}"]))

    for doc_id in sorted(docs):
        r = await call("database_query", {"query_name": "chunks_by_document", "parameters": {"doc_id": doc_id},
                                          "limit": 500})
        want = catalog.by_doc.get(doc_id, [])
        got = r["out"]["rows"] if not r["is_error"] else None
        problems = []
        if got is None or [x["chunk_id"] for x in got] != [c.chunk_id for c in want]:
            problems.append("chunk ids or order differ")
        else:
            for x, c in zip(got, want):
                if (x["ordinal"], x["pages"], x["section_path"], x["clause_id"], x["content_hash"]) != (
                        c.ordinal, c.pages, c.section_path, c.clause_id, c.content_hash):
                    problems.append(f"{c.chunk_id} fields differ")
        rows.append(row(f"chunks_by_document {doc_id}", problems, rows=len(got or [])))

    biggest = max(docs, key=lambda i: docs[i].chunks)
    r = await call("database_query", {"query_name": "chunks_by_document", "parameters": {"doc_id": biggest},
                                      "limit": 7})
    o = r["out"]
    rows.append(row("truncation", [] if (o["row_count"], o["truncated"]) == (7, True) and
                    [x["chunk_id"] for x in o["rows"]] == [c.chunk_id for c in catalog.by_doc[biggest][:7]]
                    else [f"{o['row_count']} {o['truncated']}"]))
    hostile, _ = await ids({"query_name": "documents", "parameters": {"organization": "x' OR '1'='1"}})
    still, _ = await ids({"query_name": "documents"})
    rows.append(row("hostile-value", [] if hostile == [] and len(still) == len(docs) else ["hostile value matched"]))
    return rows


async def failure_suite(catalog, settings) -> list[dict]:
    from mcp import Client

    from services.mcp.server import build_server
    from services.mcp.tools import Tools
    from services.persistence.config import DatabaseSettings
    from services.mcp.postgres import PostgresDatabase

    cases = [
        ("no-database-url", None, {"query_name": "documents"}, "database_not_configured", "DATABASE_URL"),
        ("closed-port", DatabaseSettings(url="postgresql://u:pw@127.0.0.1:1/x", connect_timeout_s=2),
         {"query_name": "documents"}, "database_unavailable", "cannot connect"),
        ("unmigrated-schema", DatabaseSettings(url=settings.url, schema=f"{SCHEMA}_empty"),
         {"query_name": "documents"}, "database_unavailable", "is not initialized"),
        ("unloaded-schema", DatabaseSettings(url=settings.url, schema=f"{SCHEMA}_unloaded"),
         {"query_name": "documents"}, "database_unavailable", "no corpus is loaded"),
        ("mistyped-parameter", settings, {"query_name": "documents", "parameters": {"is_current": "true"}},
         "invalid_arguments", "is_current must be bool"),
        ("unknown-parameter", settings, {"query_name": "documents", "parameters": {"owner": "x"}},
         "invalid_arguments", "unknown parameters"),
        ("sql-text", settings, {"query_name": "documents", "sql": "SELECT 1"}, "invalid_arguments", "1 invalid"),
        ("missing-required", settings, {"query_name": "document_versions"}, "invalid_arguments", "1 invalid"),
    ]
    rows = []
    for cid, s, args, code, text in cases:
        server = build_server(Tools(catalog, None, PostgresDatabase(s) if s else None))
        async with Client(server) as client:
            r = await client.call_tool("database_query", args)
        err = r.structured_content.get("error", {}) if r.is_error else {}
        msg = err.get("message", "") + json.dumps(err.get("details", []))
        problems = [] if r.is_error and err.get("code") == code and text in msg else [f"got {r.structured_content}"]
        if "pw" in json.dumps(err) and s is not None and ":pw@" in s.url:
            problems.append("password in the error")
        rows.append(row(cid, problems, expected=code, got=err.get("code", "accepted")))
    return rows


def write_md(suites: dict, meta: dict) -> str:
    out = ["# Phase 9 evaluation (PostgreSQL behind database_query; no language model)", "",
           f"Database: {meta['database']} (PostgreSQL {meta['server_version']}), schema `{SCHEMA}` rebuilt for the "
           "run. Every query through the MCP protocol (mcp.Client → services.mcp.server → PostgresDatabase); "
           "expectations from the repository metadata (MetadataCatalog over data/). See evaluation/database/run.py.",
           ""]
    total = [r for rows in suites.values() for r in rows]
    out += [f"## {sum(not r['checks'] for r in total)}/{len(total)} checks passed", ""]
    for name, rows in suites.items():
        out += [f"### {name}: {sum(not r['checks'] for r in rows)}/{len(rows)}", "", "| case | detail | checks |",
                "|---|---|---|"]
        for r in rows:
            detail = {k: v for k, v in r.items() if k not in ("id", "checks")}
            text = ", ".join(f"{k}={v}" for k, v in detail.items())
            out.append(f"| {r['id']} | {text[:160]} | {'; '.join(r['checks']) or 'ok'} |")
        out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    import logging

    from importlib.metadata import version

    import anyio
    from mcp import Client

    from services.mcp.catalog import MetadataCatalog
    from services.mcp.postgres import PostgresDatabase
    from services.mcp.server import build_server
    from services.mcp.tools import Tools
    from services.persistence import migrate as M
    from services.persistence.config import DatabaseSettings
    from services.persistence.connection import connect

    sys.stdout.reconfigure(encoding="utf-8")
    logging.disable(logging.INFO)
    env = DatabaseSettings.from_env()
    if env is None:
        print("DATABASE_URL is not set (see docs/database.md)", file=sys.stderr)
        return 2
    settings = DatabaseSettings(url=env.url, schema=SCHEMA, connect_timeout_s=env.connect_timeout_s,
                                statement_timeout_ms=env.statement_timeout_ms)
    with connect(settings) as conn:
        for schema in (SCHEMA, f"{SCHEMA}_empty", f"{SCHEMA}_unloaded"):
            M.drop_schema(conn, schema)
        server_version = conn.execute("SHOW server_version").fetchone()[0]
    with connect(DatabaseSettings(url=env.url, schema=f"{SCHEMA}_unloaded")) as conn:
        M.apply(conn, f"{SCHEMA}_unloaded")

    catalog = MetadataCatalog.load()
    suites = {"schema": schema_suite(settings), "load": load_suite(settings, catalog)}

    async def run():
        async with Client(build_server(Tools(catalog, None, PostgresDatabase(settings)))) as client:
            q = await query_suite(Calls(client), catalog)
        return q, await failure_suite(catalog, settings)

    suites["queries"], suites["failures"] = anyio.run(run)
    with connect(settings) as conn:
        for schema in (f"{SCHEMA}_empty", f"{SCHEMA}_unloaded"):
            M.drop_schema(conn, schema)
    meta = {"database": env.redacted_url(), "server_version": server_version, "schema": SCHEMA,
            "documents": len(catalog.doc_ids()), "chunks": len(catalog.chunks), "psycopg": version("psycopg")}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "postgres.json").write_text(json.dumps({"meta": meta, "suites": suites}, indent=1, ensure_ascii=False,
                                                      default=str) + "\n", encoding="utf-8")
    md = write_md(suites, meta)
    (RESULTS / "postgres.md").write_text(md, encoding="utf-8")
    print(md)
    return 1 if any(r["checks"] for rows in suites.values() for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
