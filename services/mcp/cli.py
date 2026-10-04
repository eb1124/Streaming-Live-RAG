"""A command-line MCP client for the phase 8 server (the `mcp` SDK's Client), for manual testing.

  python -m services.mcp.cli list                                       tools and their schemas
  python -m services.mcp.cli call document_search -a "query=..." -j limit=3
  python -m services.mcp.cli call metadata_lookup -a organization=UConn
  python -m services.mcp.cli call document_search --args-file args.json
  python -m services.mcp.cli call no_such_tool                          (shows the protocol error)

  --url URL      a running server (default http://127.0.0.1:8003/mcp)
  --stdio        start `python -m services.mcp --transport stdio` as a subprocess instead (--retrieval and the
                 DATABASE_*, MCP_*, RETRIEVAL_*, OTEL_*, SERVICE_TIMEOUT_S environment variables passed on)
  -a key=value   a string argument;  -j key=<json>  a typed argument (number, true/false, null, list, object)
  --summary      document_search: one line per result instead of the full JSON

Prints the result as JSON: {"is_error", "structured_content"} (or {"protocol_error": {...}}).
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def parse_arguments(args) -> dict:
    out: dict = {}
    if args.args_file:
        with open(args.args_file, encoding="utf-8-sig") as f:
            out.update(json.load(f))
    for item in args.a or []:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"-a expects key=value, got {item!r}")
        out[key] = value
    for item in args.j or []:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"-j expects key=<json>, got {item!r}")
        out[key] = json.loads(value)
    return out


def summary(sc: dict) -> str:
    lines = [f"query: {sc['query']}", f"backend: {sc['backend']}  mode: {sc['mode']}  candidates: {sc['candidates']}  "
             f"filtered_out: {sc['filtered_out']}  temporal: {sc['temporal']['kind']} {sc['temporal']['selected']}",
             f"correlation: {sc['correlation']}"]
    for r in sc["results"]:
        d, loc, s = r["document"], r["location"], r["score"]
        pages = f"p{loc['page_start']}" + (f"-{loc['page_end']}" if loc["page_end"] != loc["page_start"] else "")
        lines.append(f"#{r['rank']} (retrieval #{s['retrieval_rank']}, score {s['value']:.3f}) {r['chunk_id']}")
        lines.append(f"    {d['organization']} | {d['title']} | effective {d['effective_date']} | "
                     f"current {d['is_current']} | {pages} | clause {loc['clause_id']}")
        lines.append(f"    {d['filename']}")
    lines.extend(f"note: {n}" for n in sc.get("notes", []))
    return "\n".join(lines)


async def run(args) -> int:
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    from mcp.shared.exceptions import MCPError

    if args.stdio:
        # The SDK starts the server with a minimal environment; pass on the server's own settings (DATABASE_URL, ...)
        settings = {k: v for k, v in os.environ.items()
                    if k.startswith(("DATABASE_", "MCP_", "RETRIEVAL_", "OTEL_")) or k == "SERVICE_TIMEOUT_S"}
        target = StdioServerParameters(command=sys.executable, env=settings,
                                       args=["-m", "services.mcp", "--transport", "stdio", "--retrieval", args.retrieval])
    else:
        target = args.url
    async with Client(target) as client:
        if args.command == "list":
            tools = (await client.list_tools()).tools
            body = [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools]
            if not args.schemas:
                body = [{"name": t["name"], "title": t.get("title"), "description": t.get("description"),
                         "input": sorted(t["inputSchema"].get("properties", {})),
                         "required": t["inputSchema"].get("required", []),
                         "output": sorted(t.get("outputSchema", {}).get("properties", {}))} for t in body]
            print(json.dumps(body, indent=1, ensure_ascii=False))
            return 0
        try:
            result = await client.call_tool(args.tool, parse_arguments(args))
        except MCPError as e:
            print(json.dumps({"protocol_error": e.error.model_dump(mode="json", exclude_none=True)}, indent=1))
            return 2
        if args.summary and not result.is_error and args.tool == "document_search":
            print(summary(result.structured_content))
        else:
            print(json.dumps({"is_error": bool(result.is_error), "structured_content": result.structured_content},
                             indent=1, ensure_ascii=False))
        return 1 if result.is_error else 0


def main(argv=None) -> int:
    import anyio

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8003/mcp")
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--retrieval", choices=["remote", "local"], default="remote")
    sub = parser.add_subparsers(dest="command", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--schemas", action="store_true", help="print the full input and output JSON schemas")
    c = sub.add_parser("call")
    c.add_argument("tool")
    c.add_argument("-a", action="append", metavar="KEY=VALUE")
    c.add_argument("-j", action="append", metavar="KEY=JSON")
    c.add_argument("--args-file")
    c.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    return anyio.run(run, args)


if __name__ == "__main__":
    raise SystemExit(main())
