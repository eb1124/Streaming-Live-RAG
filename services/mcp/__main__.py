"""python -m services.mcp      the AdaptiveRAG MCP server (phase 8)

  --transport http    Streamable HTTP on MCP_HOST:MCP_PORT (default 127.0.0.1:8003), endpoint /mcp; GET /health
  --transport stdio   over stdin/stdout (an MCP client starts the process; logs go to stderr)
  --retrieval remote  document_search calls the phase 7 retrieval service at RETRIEVAL_URL (default; start it first:
                      python -m services.retrieval). Nothing is loaded here but the chunk metadata.
  --retrieval local   loads the frozen retrieval stack in this process (arctic-m, BM25, cross-encoder) instead

Environment: MCP_HOST, MCP_PORT, MCP_RETRIEVAL, RETRIEVAL_URL, SERVICE_TIMEOUT_S (services/config.py). Needs the
"mcp" and "services" extras (and "retrieval" for --retrieval local).
database_query: PostgreSQL when DATABASE_URL is set (phase 9, services/persistence; DATABASE_SCHEMA and the timeouts
in services/persistence/config.py; needs the "postgres" extra), otherwise NotConfigured (database_not_configured).
"""

from __future__ import annotations

import argparse
import logging
import os


def build_tools(retrieval: str, retrieval_url: str, timeout_s: float):
    from .backends import LocalSearch, RemoteSearch
    from .catalog import MetadataCatalog
    from .tools import Tools

    catalog = MetadataCatalog.load()
    if retrieval == "local":
        from generation.pipeline import load_stack

        from ..retrieval.service import RetrievalService

        search = LocalSearch(RetrievalService(load_stack(rerank=True)))
    else:
        import httpx

        search = RemoteSearch(httpx.Client(base_url=retrieval_url, timeout=timeout_s))
    return Tools(catalog, search, build_database())


def build_database():
    """The PostgreSQL adapter when DATABASE_URL is set, else None (Tools then uses NotConfigured)."""
    from ..persistence.config import DatabaseSettings

    settings = DatabaseSettings.from_env()
    if settings is None:
        return None
    from .postgres import PostgresDatabase

    return PostgresDatabase(settings)


def main(argv=None) -> int:
    import anyio

    from .. import telemetry
    from ..config import Settings
    from .server import SPECS, build_server

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--transport", choices=["http", "stdio"], default="http")
    parser.add_argument("--retrieval", choices=["remote", "local"], default=os.environ.get("MCP_RETRIEVAL") or "remote")
    parser.add_argument("--host", default=os.environ.get("MCP_HOST") or "127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT") or 8003))
    args = parser.parse_args(argv)

    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    telemetry.configure(telemetry.MCP)
    s = Settings.from_env()
    tools = build_tools(args.retrieval, s.retrieval_url, s.service_timeout_s)
    server = build_server(tools)
    log = logging.getLogger("services.mcp")
    log.info("MCP server: %d documents, %d chunks, retrieval %s, tools %s", len(tools.catalog.doc_ids()),
             len(tools.catalog.chunks), tools.search.name, sorted(SPECS))

    if args.transport == "stdio":
        from mcp.server.stdio import stdio_server

        async def serve():
            async with stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())

        anyio.run(serve)
        return 0

    import uvicorn
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "mcp", "tools": sorted(SPECS), "retrieval": tools.search.name,
                             "documents": len(tools.catalog.doc_ids()), "chunks": len(tools.catalog.chunks),
                             "database": tools.database.name})

    app = server.streamable_http_app(streamable_http_path="/mcp", host=args.host,
                                     custom_starlette_routes=[Route("/health", health, methods=["GET"])])
    log.info("Streamable HTTP on http://%s:%d/mcp", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
