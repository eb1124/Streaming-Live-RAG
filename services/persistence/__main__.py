"""python -m services.persistence migrate | load | status      (phase 9; needs DATABASE_URL and the "postgres" extra)

  migrate   create the schema (DATABASE_SCHEMA, default adaptiverag) and apply pending migrations; idempotent
  load      copy the repository metadata (data/ingested, data/chunks) into the tables, replacing the previous copy
  status    applied / pending migrations, the latest load, row counts

Exit code 0 on success, 1 on a database or migration error (the message names the cause), 2 without DATABASE_URL.
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["migrate", "load", "status"])
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    import psycopg

    from . import migrate as M
    from .config import DatabaseSettings
    from .connection import DatabaseError, connect

    settings = DatabaseSettings.from_env()
    if settings is None:
        print("DATABASE_URL is not set (see docs/database.md)", file=sys.stderr)
        return 2
    try:
        with connect(settings) as conn:
            if args.command == "migrate":
                done = M.apply(conn, settings.schema)
                out = {"schema": settings.schema, "applied": [f"{m.version:04d}_{m.name}" for m in done],
                       "up_to_date": not done}
            elif args.command == "load":
                from .load import load, read_repository

                pending = M.check(conn, M.migrations())
                if pending:
                    raise M.MigrationError(f"{len(pending)} pending migration(s): run migrate first")
                out = load(conn, read_repository())
            else:
                files = M.migrations()
                (initialized,) = conn.execute("SELECT to_regclass('schema_migrations') IS NOT NULL").fetchone()
                done = M.applied(conn) if initialized else {}
                out = {"database": settings.redacted_url(), "schema": settings.schema, "initialized": initialized,
                       "applied": [f"{v:04d}_{n}" for v, (n, _) in done.items()],
                       "pending": [f"{m.version:04d}_{m.name}" for m in (M.check(conn, files) if initialized
                                                                          else files)]}
                if 1 in done:
                    row = conn.execute("SELECT load_id, loaded_at, documents, chunks FROM corpus_loads "
                                       "ORDER BY load_id DESC LIMIT 1").fetchone()
                    out["latest_load"] = None if row is None else dict(zip(("load_id", "loaded_at", "documents",
                                                                            "chunks"), row))
                    out["rows"] = dict(zip(("documents", "chunks"), conn.execute(
                        "SELECT (SELECT count(*) FROM documents), (SELECT count(*) FROM chunks)").fetchone()))
    except (DatabaseError, M.MigrationError, psycopg.Error) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
