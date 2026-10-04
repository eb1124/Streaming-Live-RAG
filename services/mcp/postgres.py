"""The PostgreSQL adapter of database_query (phase 9): services.mcp.database.DatabaseAdapter over the phase 9
metadata repository (services/persistence/repository.py).

  PostgresDatabase(DatabaseSettings.from_env()).execute(DatabaseQueryInput, Correlation) -> DatabaseQueryOutput

The input is already validated by the phase 8 contract (named query, its parameters, their types, the limit). The
repository runs the query's fixed statement; any failure of the configured database (unreachable, schema not
migrated, no corpus loaded, statement failed) becomes DatabaseFailure, which the tool reports as
"database_unavailable" with the repository's message.
"""

from __future__ import annotations

from services.persistence.config import DatabaseSettings
from services.persistence.connection import DatabaseError
from services.persistence.queries import NAMED_QUERIES
from services.persistence.repository import MetadataRepository

from .contracts import QUERIES, Correlation, DatabaseQueryInput, DatabaseQueryOutput
from .database import DatabaseFailure

if {n: set(q.parameters) for n, q in NAMED_QUERIES.items()} != {n: set(p) for n, p in QUERIES.items()}:
    raise RuntimeError("services/persistence/queries.py and the database_query contract (QUERIES) disagree")


class PostgresDatabase:
    name = "postgresql"

    def __init__(self, settings: DatabaseSettings, connector=None):
        self.settings = settings
        self.repository = MetadataRepository(settings, connector)

    def execute(self, req: DatabaseQueryInput, correlation: Correlation) -> DatabaseQueryOutput:
        try:
            r = self.repository.query(req.query_name, req.parameters, req.limit)
        except DatabaseError as e:
            raise DatabaseFailure(str(e)) from e
        return DatabaseQueryOutput(correlation=correlation, query_name=req.query_name, source=self.name,
                                   columns=r.columns, rows=r.rows, row_count=len(r.rows), truncated=r.truncated)
