"""The metadata repository (phase 9): named queries against PostgreSQL, independent of any caller (MCP or other).

  MetadataRepository(settings).query("document_versions", {"series_id": "..."}, limit=50) -> QueryResult

Per call: one connection (connection.connect: connect timeout, statement timeout, search_path), a read-only
transaction, a check that a corpus has been loaded, the query's fixed statement with bound parameters (queries.py),
at most limit + 1 rows, close. Failures raise a DatabaseError subclass naming the cause; nothing is retried.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .. import telemetry
from .config import DatabaseSettings
from .connection import DatabaseError, _first_line, connect
from .queries import bind

log = logging.getLogger(__name__)


class SchemaNotInitialized(DatabaseError):
    pass


class CorpusNotLoaded(DatabaseError):
    pass


class StatementFailed(DatabaseError):
    pass


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[dict]
    truncated: bool


class MetadataRepository:
    def __init__(self, settings: DatabaseSettings, connector=None):
        self.settings, self.connector = settings, connector

    def query(self, name: str, parameters: dict, limit: int) -> QueryResult:
        import psycopg

        q, params = bind(name, parameters, limit)
        # phase 10A: the named query, never its SQL, its parameters, the connection URL or a returned row
        with telemetry.span("db.query", telemetry.PERSISTENCE, {
                "db.system.name": "postgresql", "db.operation.name": "SELECT", "db.query.name": q.name,
                "db.namespace": self.settings.schema}) as span:
            conn = connect(self.settings, self.connector)
            try:
                conn.read_only = True
                with conn.transaction():
                    (loaded,) = conn.execute("SELECT EXISTS (SELECT 1 FROM corpus_loads)").fetchone()
                    if not loaded:
                        raise CorpusNotLoaded(f"no corpus is loaded in schema {self.settings.schema!r}: run "
                                              "python -m services.persistence load")
                    rows = conn.execute(q.sql, params).fetchall()
            except psycopg.errors.UndefinedTable as e:
                raise SchemaNotInitialized(f"schema {self.settings.schema!r} is not initialized: run "
                                           "python -m services.persistence migrate, then load") from e
            except psycopg.Error as e:
                raise StatementFailed(f"{name} failed: {_first_line(e)}") from e
            finally:
                conn.close()
            truncated = len(rows) > limit
            rows = rows[:limit]
            span.set({"db.response.returned_rows": len(rows), "db.query.truncated": truncated})
        log.info("query %s rows=%d truncated=%s", name, len(rows), truncated)
        return QueryResult(list(q.columns), [dict(zip(q.columns, r)) for r in rows], truncated)
