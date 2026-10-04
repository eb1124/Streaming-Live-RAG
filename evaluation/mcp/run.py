"""Phase 8 offline evaluation: the MCP tools over the real retrieval stack and the real repository metadata, through
the MCP protocol (mcp.Client connected in memory to services.mcp.server). Deterministic: no language model, no
network.

  python -m evaluation.mcp.run            # writes results/offline.json and results/offline.md

Suites:
  search      the 27 integration questions and the 16 phase 2 questions: document_search (limit 10) equals the
              frozen retrieval (generation.pipeline.retrieve) chunk for chunk (ids, order, scores, retriever,
              temporal resolution); every result's provenance equals the chunk file and the ingestion manifest;
              integration gold evidence found in the top 10 (same as the direct retrieval by construction)
  remote      the same questions through the phase 7 retrieval service (RemoteSearch over its FastAPI app):
              output identical to the local backend, correlation ids received by the retrieval service
  filters     organization / doc_id / current_only / as_of cases on the UConn series and other organizations
  metadata    metadata_lookup against data/ingested/_manifest.json, data/chunks and config/organization_aliases.toml
  rejections  malformed arguments, unknown names, unknown tool, database_query without a database
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
ROOT = HERE.parents[1]
FEB = "travel-and-entertainment-procedures-final-ccccf9"
JUL = "2026-07-01-travel-and-entertainment-procedures-ca903b"
NEUTRAL_CARD = ("How long do UConn travelers have to submit University Travel Card charges before the card is "
                "suspended?")


class Calls:
    """One MCP client session; every call recorded as (tool, arguments, is_error, structured content)."""

    def __init__(self, client):
        self.client = client

    async def __call__(self, tool: str, arguments: dict):
        from mcp.shared.exceptions import MCPError

        try:
            r = await self.client.call_tool(tool, arguments)
        except MCPError as e:
            return {"protocol_error": {"code": e.code, "message": e.error.message}}
        return {"is_error": bool(r.is_error), "out": r.structured_content}


def questions():
    from evaluation.integration.cases import CASES as INTEGRATION
    from evaluation.multi_intent.cases import CASES as P2

    return [(c["id"], c["question"], c) for c in INTEGRATION] + [(c["id"], c["question"], None) for c in P2]


def provenance_problems(result: dict, chunk, manifest: dict) -> list[str]:
    d, loc, m = result["document"], result["location"], manifest.get(chunk.doc_id, {})
    expected = {
        "text": (result["text"], chunk.text), "content_hash": (result["content_hash"], chunk.content_hash),
        "doc_id": (d["doc_id"], chunk.doc_id), "title": (d["title"], chunk.title),
        "organization": (d["organization"], chunk.organization), "version": (d["version"], chunk.version),
        "effective_date": (d["effective_date"], chunk.effective_date),
        "superseded_date": (d["superseded_date"], chunk.superseded_date),
        "is_current": (d["is_current"], chunk.is_current), "series_id": (d["series_id"], chunk.series_id),
        "filename": (d["filename"], m.get("filename")), "page_count": (d["page_count"], m.get("page_count")),
        "pages": ((loc["page_start"], loc["page_end"], loc["pages"]), (chunk.page_start, chunk.page_end, chunk.pages)),
        "section_path": (loc["section_path"], chunk.section_path), "clause_id": (loc["clause_id"], chunk.clause_id),
    }
    problems = [k for k, (got, want) in expected.items() if got != want]
    if not d["filename"]:
        problems.append("no filename")
    return problems


async def search_suite(call, stack, manifest, direct_cache) -> list[dict]:
    from evaluation.integration.cases import resolve
    from chunking.pipeline import load_chunks

    by_id = {c.chunk_id: c for c in stack.chunks}
    cbd = load_chunks()
    rows = []
    for cid, q, case in questions():
        direct = direct_cache[q]
        r = await call("document_search", {"query": q, "limit": 10, "session_id": f"eval-{cid}"})
        checks = []
        if r.get("is_error") is not False:
            rows.append({"id": cid, "question": q, "checks": [f"error: {r}"]})
            continue
        out = r["out"]
        got = [(x["chunk_id"], x["score"]["value"], x["score"]["retriever"]) for x in out["results"]]
        want = [(e.chunk.chunk_id, e.score, e.retriever) for e in direct.evidence[:10]]
        if got != want:
            checks.append("results differ from the frozen retrieval")
        res = direct.resolution
        if (out["temporal"]["kind"], out["temporal"]["selected"], out["temporal"]["dropped"]) != (
                res.intent.kind, res.selected, len(res.dropped)):
            checks.append("temporal resolution differs")
        bad = {x["chunk_id"]: p for x in out["results"] if (p := provenance_problems(x, by_id[x["chunk_id"]], manifest))}
        if bad:
            checks.append(f"provenance: {bad}")
        if out["correlation"]["session_id"] != f"eval-{cid}":
            checks.append("session id not echoed")
        gold = None
        if case is not None and case["gold"]:
            ids = [x["chunk_id"] for x in out["results"]]
            units = [{resolve(ref, cbd) for ref in unit} for unit in case["gold"]]
            gold = f"{sum(bool(u & set(ids)) for u in units)}/{len(units)}"
        top = out["results"][0] if out["results"] else None
        rows.append({"id": cid, "question": q, "results": len(out["results"]), "temporal": out["temporal"]["kind"],
                     "selected": out["temporal"]["selected"], "gold_in_top10": gold,
                     "top": None if top is None else {"chunk_id": top["chunk_id"],
                                                      "organization": top["document"]["organization"],
                                                      "filename": top["document"]["filename"],
                                                      "pages": top["location"]["pages"],
                                                      "clause": top["location"]["clause_id"],
                                                      "effective_date": top["document"]["effective_date"],
                                                      "score": top["score"]["value"]},
                     "checks": checks})
    return rows


async def remote_suite(local_call, remote_call, recorded) -> list[dict]:
    rows = []
    for cid, q, _ in questions():
        ids = {"request_id": f"req-{cid}", "job_id": f"job-{cid}", "session_id": f"sess-{cid}"}
        a = await local_call("document_search", {"query": q, "limit": 10, **ids})
        n = len(recorded)
        b = await remote_call("document_search", {"query": q, "limit": 10, **ids})
        checks = []
        if a.get("is_error") or b.get("is_error") or "out" not in b:
            checks.append(f"error: local {a.get('is_error')} remote {b}")
        else:
            strip = lambda o: {k: v for k, v in o.items() if k != "backend"}  # noqa: E731
            if strip(a["out"]) != strip(b["out"]):
                checks.append("remote output differs from local")
            if not b["out"]["backend"].startswith("remote "):
                checks.append("not served by the retrieval service")
            if [x.ids() for x in recorded[n:]] != [ids]:
                checks.append(f"retrieval service received {[x.ids() for x in recorded[n:]]}")
        rows.append({"id": cid, "checks": checks})
    return rows


def as_of_problems(o: dict, day: str) -> list[str]:
    """No result applies after `day` by its own metadata: no full effective date after it, no full superseded date on
    or before it. Results without a full effective date must be flagged undetermined and named in a note."""
    from datetime import date

    def iso(v):
        try:
            return date.fromisoformat(v) if v else None
        except ValueError:
            return None

    d, problems = date.fromisoformat(day), []
    if not o["results"]:
        problems.append("no results")
    for x in o["results"]:
        doc = x["document"]
        eff, sup = iso(doc["effective_date"]), iso(doc["superseded_date"])
        if eff is not None and eff > d:
            problems.append(f"{doc['doc_id']} effective {eff} after {day}")
        if sup is not None and sup <= d:
            problems.append(f"{doc['doc_id']} superseded {sup}")
        if x["as_of_applicability"] != ("in_force" if eff is not None else "undetermined"):
            problems.append(f"{x['chunk_id']} flagged {x['as_of_applicability']}")
    undetermined = sorted({x["document"]["doc_id"] for x in o["results"] if x["as_of_applicability"] == "undetermined"})
    note = next((n for n in o["notes"] if n.startswith(f"as_of {day}")), "")
    if undetermined and not all(u in note for u in undetermined):
        problems.append("undetermined documents not named in a note")
    return problems


FILTER_CASES = [
    # (id, arguments, expectation(out) -> list of problems)
    ("org-short-name", {"query": NEUTRAL_CARD, "organization": "UConn", "limit": 10},
     lambda o: [] if o["results"] and {x["document"]["organization"] for x in o["results"]} ==
     {"University of Connecticut"} and o["filters"]["organization"] == "University of Connecticut" else ["org"]),
    ("current-only", {"query": NEUTRAL_CARD, "current_only": True, "limit": 20},
     lambda o: [] if o["results"] and all(x["document"]["doc_id"] != FEB for x in o["results"])
     and any(x["document"]["doc_id"] == JUL for x in o["results"]) else ["current_only"]),
    ("as-of-march", {"query": NEUTRAL_CARD, "as_of": "2026-03-15", "limit": 20},
     lambda o: as_of_problems(o, "2026-03-15") + ([] if any(x["document"]["doc_id"] == FEB for x in o["results"])
                                                  else ["no February procedures"])),
    ("as-of-march-other-orgs", {"query": "What purchase amount requires competitive bids?", "as_of": "2026-03-15",
                                "limit": 20},
     lambda o: as_of_problems(o, "2026-03-15")),
    ("as-of-august", {"query": NEUTRAL_CARD, "as_of": "2026-08-01", "limit": 20},
     lambda o: as_of_problems(o, "2026-08-01") + ([] if any(x["document"]["doc_id"] == JUL for x in o["results"])
                                                  else ["no July procedures"])),
    ("doc-id", {"query": NEUTRAL_CARD, "doc_id": FEB, "limit": 20},
     lambda o: [] if o["results"] and {x["document"]["doc_id"] for x in o["results"]} == {FEB} else ["doc_id"]),
    ("superseded-doc-current-only-empty", {"query": NEUTRAL_CARD, "doc_id": FEB, "current_only": True},
     lambda o: [] if o["results"] == [] and o["filtered_out"] == o["candidates"] > 0 and o["notes"] else ["empty"]),
    ("other-org-not-in-pool", {"query": NEUTRAL_CARD, "organization": "Yale"},
     lambda o: [] if all(x["document"]["organization"] == "Yale University" for x in o["results"])
     and o["filtered_out"] + len(o["results"]) <= o["candidates"] else ["other org"]),
    ("rutgers-question-rutgers-filter",
     {"query": "How far in advance of departure must a Rutgers travel advance request be submitted?",
      "organization": "Rutgers University", "limit": 5},
     lambda o: [] if o["results"] and {x["document"]["organization"] for x in o["results"]} ==
     {"Rutgers University"} else ["rutgers"]),
    ("ranks-keep-retrieval-order", {"query": NEUTRAL_CARD, "organization": "UConn", "current_only": True, "limit": 20},
     lambda o: [] if [x["score"]["retrieval_rank"] for x in o["results"]] ==
     sorted(x["score"]["retrieval_rank"] for x in o["results"]) else ["order"]),
]


async def filter_suite(call) -> list[dict]:
    rows = []
    for cid, args, expect in FILTER_CASES:
        r = await call("document_search", args)
        if r.get("is_error") is not False:
            rows.append({"id": cid, "arguments": args, "checks": [f"error: {r}"]})
            continue
        o = r["out"]
        rows.append({"id": cid, "arguments": args, "candidates": o["candidates"], "filtered_out": o["filtered_out"],
                     "results": [(x["chunk_id"], x["score"]["retrieval_rank"]) for x in o["results"]],
                     "notes": o["notes"], "checks": expect(o)})
    return rows


async def metadata_suite(call, stack, manifest) -> list[dict]:
    from adaptive.multi.decompose import load_aliases
    from chunking.pipeline import load_manifest

    rows = []
    counts = {}
    for c in stack.chunks:
        counts[c.doc_id] = counts.get(c.doc_id, 0) + 1
    excluded = {d["doc_id"]: d["excluded_records"] for d in load_manifest()["documents"]}

    r = await call("metadata_lookup", {"include_provenance": True})
    docs = {d["doc_id"]: d for d in r["out"]["documents"]}
    checks = [] if sorted(docs) == sorted(manifest) else ["document set differs from the manifest"]
    for doc_id, m in manifest.items():
        d = docs.get(doc_id)
        if d is None:
            continue
        for field in ("filename", "title", "organization", "page_count", "file_sha256", "content_hash", "status",
                      "revision_date", "source_url"):
            if d[field] != m.get(field):
                checks.append(f"{doc_id}: {field}")
        if d["effective_date_text"] != m.get("effective_date") or d["provenance"] != m.get("metadata_provenance"):
            checks.append(f"{doc_id}: dates/provenance")
        if d["chunks"] != counts.get(doc_id, 0) or d["excluded_records"] != excluded.get(doc_id):
            checks.append(f"{doc_id}: counts")
    rows.append({"id": "all-documents", "documents": len(docs), "checks": checks})

    for alias, org in sorted(load_aliases().items()):
        r = await call("metadata_lookup", {"organization": alias})
        want = sorted(i for i, m in manifest.items() if m.get("organization") == org)
        got = [d["doc_id"] for d in r["out"]["documents"]] if not r["is_error"] else None
        rows.append({"id": f"organization {alias}", "documents": got,
                     "checks": [] if got == want and r["out"]["organization"] == org else [f"expected {want}"]})

    r = await call("metadata_lookup", {"series_id": "uconn-travel-entertainment-procedures"})
    got = [(d["doc_id"], d["effective_date"], d["superseded_date"], d["is_current"]) for d in r["out"]["documents"]]
    want = [(JUL, "2026-07-01", None, True), (FEB, "2026-02-01", "2026-07-01", False)]
    series = r["out"]["documents"][0]["series_versions"] if got else []
    rows.append({"id": "uconn-series", "documents": got,
                 "checks": [] if got == want and series == [FEB, JUL] else ["series"]})

    r = await call("metadata_lookup", {"current_only": True, "organization": "University of Connecticut"})
    got = [d["doc_id"] for d in r["out"]["documents"]]
    rows.append({"id": "uconn-current", "documents": got, "checks": [] if FEB not in got and JUL in got else ["cur"]})

    chunk = next(c for c in stack.chunks if c.doc_id == FEB and c.prev_chunk_id)
    r = await call("metadata_lookup", {"chunk_id": chunk.chunk_id, "include_text": True})
    c = r["out"]["chunk"]
    ok = (c["text"] == chunk.text and c["location"]["pages"] == chunk.pages and c["location"]["clause_id"] ==
          chunk.clause_id and c["prev_chunk_id"] == chunk.prev_chunk_id and r["out"]["documents"][0]["doc_id"] == FEB)
    rows.append({"id": "chunk-lookup", "chunk_id": chunk.chunk_id, "checks": [] if ok else ["chunk fields"]})

    r = await call("metadata_lookup", {"organization": "Yale", "series_id": "uconn-travel-entertainment-procedures"})
    rows.append({"id": "empty-combination", "documents": r["out"]["documents"],
                 "checks": [] if r["out"]["documents"] == [] and r["out"]["notes"] else ["empty"]})
    return rows


REJECTIONS = [
    ("search-missing-query", "document_search", {}, "invalid_arguments"),
    ("search-extra-field", "document_search", {"query": "q", "top_k": 5}, "invalid_arguments"),
    ("search-limit-too-big", "document_search", {"query": "q", "limit": 100}, "invalid_arguments"),
    ("search-bad-date", "document_search", {"query": "q", "as_of": "next week"}, "invalid_arguments"),
    ("search-both-version-filters", "document_search", {"query": "q", "as_of": "2026-03-01", "current_only": True},
     "invalid_arguments"),
    ("search-unknown-org", "document_search", {"query": "q", "organization": "Harvard University"},
     "unknown_organization"),
    ("search-unknown-doc", "document_search", {"query": "q", "doc_id": "not-a-document"}, "unknown_document"),
    ("lookup-unknown-chunk", "metadata_lookup", {"chunk_id": "nope::000"}, "not_found"),
    ("lookup-chunk-and-filter", "metadata_lookup", {"chunk_id": "x", "organization": "Yale"}, "invalid_arguments"),
    ("db-sql-text", "database_query", {"query_name": "documents", "sql": "DROP TABLE documents"},
     "invalid_arguments"),
    ("db-missing-parameter", "database_query", {"query_name": "chunks_by_document"}, "invalid_arguments"),
    ("db-not-configured", "database_query", {"query_name": "chunks_by_document", "parameters": {"doc_id": FEB}},
     "database_not_configured"),
    ("unknown-tool", "shell_exec", {"cmd": "dir"}, -32602),
]


async def rejection_suite(call) -> list[dict]:
    rows = []
    for cid, tool, args, want in REJECTIONS:
        r = await call(tool, args)
        got = r["protocol_error"]["code"] if "protocol_error" in r else (
            r["out"]["error"]["code"] if r["is_error"] else "accepted")
        rows.append({"id": cid, "tool": tool, "expected": want, "got": got, "checks": [] if got == want else ["code"]})
    return rows


def write_md(suites: dict, meta: dict) -> str:
    out = ["# Phase 8 offline evaluation (MCP tools; no language model, no network)", "",
           "Every call goes through the MCP protocol (mcp.Client in memory → services.mcp.server). Real retrieval "
           "stack (arctic-m + BM25 + RRF, temporal resolution, cross-encoder rerank), real repository metadata. "
           "See evaluation/mcp/run.py.", "",
           f"Corpus: {meta['documents']} documents, {meta['chunks']} chunks. Tools listed: {meta['tools']}.", ""]
    total = [r for rows in suites.values() for r in rows]
    out += [f"## {sum(not r['checks'] for r in total)}/{len(total)} checks passed", ""]
    for name, rows in suites.items():
        out += [f"### {name}: {sum(not r['checks'] for r in rows)}/{len(rows)}", ""]
        if name == "search":
            out += ["| case | temporal | selected | results | gold in top 10 | top result (org, file, pages, clause, "
                    "effective, score) | checks |", "|---|---|---|---|---|---|---|"]
            for r in rows:
                t = r.get("top") or {}
                top = (f"{t.get('chunk_id')} ({t.get('organization')}, {t.get('filename')}, p{t.get('pages')}, "
                       f"{t.get('clause')}, {t.get('effective_date')}, {t.get('score', 0):.2f})" if t else "–")
                out.append(f"| {r['id']} | {r.get('temporal')} | {r.get('selected')} | {r.get('results')} | "
                           f"{r.get('gold_in_top10') or '–'} | {top} | {'; '.join(r['checks']) or 'ok'} |")
        elif name == "filters":
            out += ["| case | arguments | candidates | filtered out | results (chunk, retrieval rank) | checks |",
                    "|---|---|---|---|---|---|"]
            for r in rows:
                args = {k: v for k, v in r["arguments"].items() if k != "query"}
                res = ", ".join(f"{c.split('::')[-1]}@{k}" for c, k in r.get("results", [])) or "(none)"
                out.append(f"| {r['id']} | {args} | {r.get('candidates')} | {r.get('filtered_out')} | {res} | "
                           f"{'; '.join(r['checks']) or 'ok'} |")
        elif name == "rejections":
            out += ["| case | tool | expected | got | checks |", "|---|---|---|---|---|"]
            out += [f"| {r['id']} | {r['tool']} | {r['expected']} | {r['got']} | {'; '.join(r['checks']) or 'ok'} |"
                    for r in rows]
        else:
            out += ["| case | checks |", "|---|---|"]
            out += [f"| {r['id']} | {'; '.join(r['checks']) or 'ok'} |" for r in rows]
        out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    import logging
    import os

    import anyio
    from fastapi.testclient import TestClient
    from mcp import Client

    from generation.pipeline import load_stack, retrieve
    from services.mcp.backends import LocalSearch, RemoteSearch
    from services.mcp.catalog import MetadataCatalog
    from services.mcp.server import build_server
    from services.mcp.tools import Tools
    from services.retrieval.app import create_app as retrieval_app
    from services.retrieval.service import RetrievalService

    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    logging.disable(logging.INFO)
    stack = load_stack(rerank=True)
    catalog = MetadataCatalog.load()
    manifest = catalog.ingested

    class Recording(RetrievalService):
        requests: list = []

        def handle(self, req):
            Recording.requests.append(req)
            return super().handle(req)

    local = build_server(Tools(catalog, LocalSearch(RetrievalService(stack))))
    remote = build_server(Tools(catalog, RemoteSearch(TestClient(retrieval_app(Recording(stack))))))
    direct = {q: retrieve(stack, q, 20) for _, q, _ in questions()}

    async def run():
        async with Client(local) as lc, Client(remote) as rc:
            call, rcall = Calls(lc), Calls(rc)
            tools = [t.name for t in (await lc.list_tools()).tools]
            suites = {"search": await search_suite(call, stack, manifest, direct)}
            print("search done", flush=True)
            suites["remote"] = await remote_suite(call, rcall, Recording.requests)
            print("remote done", flush=True)
            suites["filters"] = await filter_suite(call)
            suites["metadata"] = await metadata_suite(call, stack, manifest)
            suites["rejections"] = await rejection_suite(call)
            return tools, suites

    tools, suites = anyio.run(run)
    meta = {"documents": len(catalog.doc_ids()), "chunks": len(catalog.chunks), "tools": tools}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "offline.json").write_text(json.dumps({"meta": meta, "suites": suites}, indent=1, ensure_ascii=False,
                                                     default=str) + "\n", encoding="utf-8")
    md = write_md(suites, meta)
    (RESULTS / "offline.md").write_text(md, encoding="utf-8")
    print(md)
    return 1 if any(r["checks"] for rows in suites.values() for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
