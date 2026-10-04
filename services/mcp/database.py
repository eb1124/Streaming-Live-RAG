"""database_query adapter contract (phase 8).

A DatabaseAdapter answers a validated DatabaseQueryInput (a named, parameterized query; never SQL text) with a
DatabaseQueryOutput. NotConfigured (no DATABASE_URL) fails every call with "database_not_configured", so the tool
can be discovered and its contract exercised, but it never returns rows it did not read from a database. The phase 9
PostgreSQL adapter (services/mcp/postgres.py, over services/persistence) implements the same protocol, one fixed
statement per QUERIES name; a configured database that cannot answer raises DatabaseFailure ("database_unavailable").
"""

from __future__ import annotations

from typing import Protocol

from .contracts import Correlation, DatabaseQueryInput, DatabaseQueryOutput


class DatabaseUnavailable(RuntimeError):
    """The adapter cannot answer. `code` is the tool error code database_query reports."""

    code = "database_not_configured"


class DatabaseFailure(DatabaseUnavailable):
    """A configured database cannot answer (unreachable, schema not migrated, no corpus loaded, statement failed)."""

    code = "database_unavailable"


class DatabaseAdapter(Protocol):
    name: str

    def execute(self, req: DatabaseQueryInput, correlation: Correlation) -> DatabaseQueryOutput: ...


class NotConfigured:
    name = "none"

    def execute(self, req: DatabaseQueryInput, correlation: Correlation) -> DatabaseQueryOutput:
        raise DatabaseUnavailable("no database adapter is configured (set DATABASE_URL; see docs/database.md)")
