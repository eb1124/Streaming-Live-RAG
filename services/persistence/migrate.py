"""Schema migrations (phase 9): numbered plain-SQL files, applied in order, recorded with their checksum.

  services/persistence/migrations/NNNN_<name>.sql     (NNNN: the version; never edit an applied file, add a new one)

apply(conn, schema) creates the schema and the schema_migrations table if needed, takes a transaction-scoped
advisory lock (two runners never interleave), refuses to continue if an applied migration's file changed or is
missing, and applies every pending file in its own transaction. Running it again applies nothing.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
FILE_PATTERN = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
LOCK_KEY = 0x41445247  # advisory lock id ("ADRG")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    out = []
    for path in sorted(directory.iterdir()):
        if path.suffix != ".sql":
            continue
        m = FILE_PATTERN.match(path.name)
        if m is None:
            raise MigrationError(f"migration file name {path.name!r} does not match NNNN_name.sql")
        body = path.read_bytes()
        out.append(Migration(int(m.group(1)), m.group(2), body.decode("utf-8"), hashlib.sha256(body).hexdigest()))
    versions = [m.version for m in out]
    if versions != list(range(1, len(out) + 1)):
        raise MigrationError(f"migration versions must be 1..n without gaps, found {versions}")
    return out


def _bootstrap(conn, schema: str) -> None:
    from psycopg import sql

    with conn.transaction():
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                            version    integer PRIMARY KEY,
                            name       text NOT NULL,
                            checksum   text NOT NULL,
                            applied_at timestamptz NOT NULL DEFAULT now())""")


def applied(conn) -> dict[int, tuple[str, str]]:
    """version -> (name, checksum) recorded in schema_migrations."""
    rows = conn.execute("SELECT version, name, checksum FROM schema_migrations ORDER BY version").fetchall()
    return {v: (n, c) for v, n, c in rows}


def check(conn, files: list[Migration]) -> list[Migration]:
    """The pending migrations; MigrationError if an applied one differs from its file or has no file."""
    done = applied(conn)
    by_version = {m.version: m for m in files}
    for version, (name, checksum) in done.items():
        m = by_version.get(version)
        if m is None:
            raise MigrationError(f"migration {version:04d}_{name} is applied but its file is missing")
        if m.checksum != checksum:
            raise MigrationError(f"migration {version:04d}_{name} was changed after it was applied "
                                 f"(checksum {checksum[:12]} in the database, {m.checksum[:12]} on disk)")
    return [m for m in files if m.version not in done]


def apply(conn, schema: str, files: list[Migration] | None = None) -> list[Migration]:
    """Apply the pending migrations; returns the ones applied (empty when the schema is up to date)."""
    files = migrations() if files is None else files
    _bootstrap(conn, schema)
    done = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        pending = check(conn, files)
        for m in pending:
            with conn.transaction():  # a savepoint per file: a failing file leaves nothing of itself
                conn.execute(m.sql)
                conn.execute("INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                             (m.version, m.name, m.checksum))
            done.append(m)
    return done


def drop_schema(conn, schema: str) -> None:
    """DROP SCHEMA ... CASCADE (tests and the evaluation rebuild their own schemas); commits."""
    from psycopg import sql

    from .config import SCHEMA_PATTERN

    if not SCHEMA_PATTERN.match(schema):
        raise ValueError(f"not a schema name: {schema!r}")
    conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
    conn.commit()
