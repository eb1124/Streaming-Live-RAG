"""PostgreSQL configuration (phase 9): environment variables only, no credentials in code.

  DATABASE_URL                    libpq URL, e.g. postgresql://adaptiverag:<password>@127.0.0.1:5433/adaptiverag
                                  (unset: no database; database_query keeps the phase 8 NotConfigured behaviour)
  DATABASE_SCHEMA                 the schema holding the tables (default adaptiverag; created by the migrations)
  DATABASE_CONNECT_TIMEOUT_S      connection timeout (default 5)
  DATABASE_STATEMENT_TIMEOUT_MS   per-statement timeout of database_query connections (default 5000)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

SCHEMA_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class DatabaseConfigError(ValueError):
    pass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class DatabaseSettings:
    url: str
    schema: str = "adaptiverag"
    connect_timeout_s: int = 5
    statement_timeout_ms: int = 5000

    def __post_init__(self):
        if not self.url:
            raise DatabaseConfigError("DATABASE_URL is empty")
        if not SCHEMA_PATTERN.match(self.schema):
            raise DatabaseConfigError(f"DATABASE_SCHEMA must match {SCHEMA_PATTERN.pattern}, not {self.schema!r}")
        if self.connect_timeout_s < 1 or self.statement_timeout_ms < 1:
            raise DatabaseConfigError("database timeouts must be positive")

    @classmethod
    def from_env(cls) -> "DatabaseSettings | None":
        """The settings, or None when DATABASE_URL is not set (no database configured)."""
        url = _env("DATABASE_URL")
        if url is None:
            return None
        return cls(url=url, schema=_env("DATABASE_SCHEMA", "adaptiverag"),
                   connect_timeout_s=int(_env("DATABASE_CONNECT_TIMEOUT_S", "5")),
                   statement_timeout_ms=int(_env("DATABASE_STATEMENT_TIMEOUT_MS", "5000")))

    def redacted_url(self) -> str:
        """The URL without its password, for logs and status output."""
        return re.sub(r"(://[^:/@]+):[^@]*@", r"\1:***@", self.url)
