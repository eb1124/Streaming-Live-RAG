"""PostgreSQL connections (phase 9). With migrate.py, load.py and postgres.py, the only code that touches psycopg.

One short-lived connection per operation (as the phase 7 orchestrator opens one broker connection per query): no
pool, nothing kept open while idle. Every connection has the configured connect timeout, statement timeout and
search_path (the schema is validated in config.py, so it can be placed in the options string).
"""

from __future__ import annotations

from .config import DatabaseSettings


class DatabaseError(RuntimeError):
    """A configured database could not do what was asked (unreachable, schema missing, statement failed)."""


def connect(settings: DatabaseSettings, connector=None):
    """A psycopg connection; `connector` replaces psycopg.connect (tests)."""
    import psycopg

    connector = connector or psycopg.connect
    options = f"-c search_path={settings.schema} -c statement_timeout={settings.statement_timeout_ms}"
    try:
        return connector(settings.url, connect_timeout=settings.connect_timeout_s, options=options,
                         application_name="adaptiverag")
    except psycopg.Error as e:
        raise DatabaseError(f"cannot connect to {settings.redacted_url()}: {_first_line(e)}") from e


def _first_line(e: Exception) -> str:
    text = str(e).strip()
    return text.splitlines()[0] if text else type(e).__name__
