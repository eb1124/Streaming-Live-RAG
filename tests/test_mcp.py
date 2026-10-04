"""MCP tool layer (phase 8, offline): discovery, schemas, document_search (results, provenance, filters, empty
results), metadata_lookup, database_query's contract, malformed arguments, unknown tools, correlation ids, the remote
retrieval service, frozen-core isolation, and no network or model calls.

Every protocol test goes through the real MCP SDK: an mcp.Client connected to the server in memory (the stdio test
starts the real server process). Retrieval is the frozen pipeline (generation.pipeline.retrieve, temporal resolution,
rerank) over the keyword-overlap stand-ins of tests/test_multi_intent_controller.py; metadata is a small manifest
over the same chunks, and the real repository metadata where data/ is present.
"""

import copy
import json
import socket
from datetime import date
import sys
from pathlib import Path

import anyio
import httpx
import jsonschema
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from mcp.shared.exceptions import MCPError

from generation.pipeline import retrieve
from services import correlation
from services.contracts import RetrievalRequest, RetrievalResponse
from services.mcp.backends import LocalSearch, RemoteSearch
from services.mcp.catalog import MetadataCatalog
from services.mcp.contracts import QUERIES, DatabaseQueryOutput
from services.mcp.server import SERVER_NAME, SPECS, build_server
from services.mcp.tools import Tools
from services.retrieval.app import create_app as retrieval_app
from services.retrieval.service import RetrievalService
from tests.test_generation import mk
from tests.test_generation_verification import FEB_CARD, JUL_CARD, SERIES
from tests.test_multi_intent_controller import stack as keyword_stack

ROOT = Path(__file__).resolve().parents[1]
ALIASES = {"TU": "Test University", "Rutgers": "Rutgers University"}
LODGE = mk("lodge", "Travelers booking a multi-bedroom accommodation must use a personal credit card.",
           doc="tu-lodging", pages=(3, 4), section=("Lodging", "Accommodation"), clause="5.1")
ADVANCE = mk("adv", "Travel advance requests must be submitted 4-6 weeks prior to departure.",
             org="Rutgers University", doc="ru", pages=(7, 7), section=("Advances",), clause="11.3")
SETTLE = mk("settle", "After the trip, documentation with itemized receipts is required to settle the travel advance.",
            org="Rutgers University", doc="ru")
CHUNKS = [FEB_CARD, JUL_CARD, LODGE, ADVANCE, SETTLE]
MANIFEST = {"pipeline_version": "ingest-test", "documents": [
    {"doc_id": "proc-feb", "filename": "Procedures-FEB.pdf", "status": "ok", "page_count": 6,
     "effective_date": "February 1, 2026", "revision_date": None, "is_current": "false", "file_sha256": "f" * 64,
     "content_hash": "a" * 64, "missing_metadata": ["version"],
     "metadata_provenance": {"effective_date": {"source": "document_text", "page": 1}}},
    {"doc_id": "proc-jul", "filename": "Procedures-JUL.pdf", "status": "ok", "page_count": 7,
     "effective_date": "July 1, 2026", "is_current": "true", "missing_metadata": []},
    {"doc_id": "tu-lodging", "filename": "Lodging.pdf", "status": "ok", "page_count": 4, "missing_metadata": []},
    {"doc_id": "ru", "filename": "Rutgers SOP.pdf", "status": "ok", "page_count": 22, "missing_metadata": []},
]}
CHUNK_MANIFEST = {"chunker_version": "chunk-test", "documents": [{"doc_id": "ru", "excluded_records": 1}]}
Q_CARD = "When is the card suspended if card charges are not submitted?"
Q_FEB = "What did TU's February 2026 procedures say about card charges not submitted?"


class RecordingService(RetrievalService):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.requests = []

    def handle(self, req):
        self.requests.append(req)
        return super().handle(req)


def setup(database=None):
    stack = keyword_stack(*CHUNKS)
    service = RecordingService(stack, ALIASES)
    catalog = MetadataCatalog(stack.chunks, MANIFEST, CHUNK_MANIFEST, ALIASES)
    return stack, service, build_server(Tools(catalog, LocalSearch(service), database))


def call(server, name, arguments=None):
    async def go():
        async with Client(server) as client:
            try:
                return await client.call_tool(name, arguments)
            except MCPError as e:  # raised outside the client's task group, not as an ExceptionGroup
                return e

    out = anyio.run(go)
    if isinstance(out, MCPError):
        raise out
    return out


def ok(server, name, arguments=None):
    r = call(server, name, arguments)
    assert not r.is_error, r.structured_content
    return r.structured_content


def error(server, name, arguments=None):
    r = call(server, name, arguments)
    assert r.is_error
    assert json.loads(r.content[0].text) == r.structured_content["error"]
    return r.structured_content["error"]


def list_tools(server):
    async def go():
        async with Client(server) as client:
            return client.server_info, (await client.list_tools()).tools
    return anyio.run(go)


@pytest.fixture
def offline(monkeypatch):
    """No network and no model: every outbound socket connection and every model / answerer call fails the test.
    (asyncio's socketpair, its internal self-pipe, is the only connection allowed.)"""
    real_connect = socket.socket.connect

    def guarded(self, address):
        if sys._getframe(1).f_code.co_name in ("socketpair", "_fallback_socketpair"):
            return real_connect(self, address)
        raise AssertionError(f"network access to {address}")

    def forbidden(*a, **kw):
        raise AssertionError("a model or answerer was called")

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr("generation.providers.GroqProvider.complete", forbidden)
    monkeypatch.setattr("generation.providers.StubProvider.complete", forbidden)
    monkeypatch.setattr("generation.answer.GroundedAnswerer.answer", forbidden)


# ---------------------------------------------------------------- discovery and schemas


def test_server_lists_exactly_the_three_tools():
    _, _, server = setup()
    info, tools = list_tools(server)
    assert info.name == SERVER_NAME
    assert [t.name for t in tools] == ["document_search", "metadata_lookup", "database_query"]
    for t in tools:
        assert t.description and t.title
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False


def test_tool_schemas_are_the_contracts_and_valid_json_schema():
    _, _, server = setup()
    _, tools = list_tools(server)
    for t in tools:
        spec = SPECS[t.name]
        assert t.input_schema == spec.input.model_json_schema(mode="validation")
        assert t.output_schema == spec.output.model_json_schema(mode="serialization")
        for schema in (t.input_schema, t.output_schema):
            jsonschema.Draft202012Validator.check_schema(schema)
            assert schema["type"] == "object"
        assert t.input_schema["additionalProperties"] is False
        assert {"request_id", "job_id", "session_id"} <= set(t.input_schema["properties"])
        assert "correlation" in t.output_schema["required"]
    by = {t.name: t for t in tools}
    assert by["document_search"].input_schema["required"] == ["query"]
    assert by["document_search"].input_schema["properties"]["limit"]["maximum"] == 20
    assert by["database_query"].input_schema["properties"]["query_name"]["enum"] == sorted(QUERIES) or \
        set(by["database_query"].input_schema["properties"]["query_name"]["enum"]) == set(QUERIES)
    assert "sql" not in json.dumps(by["database_query"].input_schema["properties"]).lower().replace("postgresql", "")


# ---------------------------------------------------------------- document_search


def test_document_search_returns_the_frozen_retrieval():
    stack, service, server = setup()
    out = ok(server, "document_search", {"query": Q_CARD, "limit": 3})
    direct = retrieve(stack, Q_CARD, 20).evidence
    assert [r["chunk_id"] for r in out["results"]] == [e.chunk.chunk_id for e in direct[:3]]
    assert [r["score"]["value"] for r in out["results"]] == [e.score for e in direct[:3]]
    assert [r["score"]["retriever"] for r in out["results"]] == [e.retriever for e in direct[:3]]
    assert [r["rank"] for r in out["results"]] == [1, 2, 3]
    assert out["candidates"] == len(direct) and out["filtered_out"] == 0
    assert out["backend"] == "local" and out["mode"] == "single" and out["rounds"] is None
    assert len(service.requests) == 1 and service.requests[0].query == Q_CARD and service.requests[0].k == 20


def test_document_search_result_provenance():
    stack, _, server = setup()
    out = ok(server, "document_search", {"query": "multi-bedroom accommodation personal credit card", "limit": 5})
    by_id = {c.chunk_id: c for c in stack.chunks}
    files = {d["doc_id"]: d for d in MANIFEST["documents"]}
    for r in out["results"]:
        c, d, loc = by_id[r["chunk_id"]], r["document"], r["location"]
        assert r["text"] == c.text and r["content_hash"] == c.content_hash
        assert d["doc_id"] == c.doc_id and d["title"] == c.title and d["organization"] == c.organization
        assert d["effective_date"] == c.effective_date and d["superseded_date"] == c.superseded_date
        assert d["is_current"] == c.is_current and d["series_id"] == c.series_id and d["version"] == c.version
        assert d["filename"] == files[c.doc_id]["filename"] and d["page_count"] == files[c.doc_id]["page_count"]
        assert (loc["page_start"], loc["page_end"], loc["pages"]) == (c.page_start, c.page_end, c.pages)
        assert loc["section_path"] == c.section_path and loc["clause_id"] == c.clause_id
    top = out["results"][0]
    assert top["chunk_id"] == LODGE.chunk_id
    assert top["location"] == {"page_start": 3, "page_end": 4, "pages": [3, 4],
                               "section_path": ["Lodging", "Accommodation"], "clause_id": "5.1", "clause_ids": [],
                               "source_block_ids": []}
    assert top["document"]["filename"] == "Lodging.pdf"


def test_document_search_reports_the_temporal_resolution():
    _, _, server = setup()
    out = ok(server, "document_search", {"query": Q_FEB})
    assert out["temporal"]["kind"] == "point_in_time"
    assert out["temporal"]["selected"] == {SERIES: ["proc-feb"]}
    assert JUL_CARD.chunk_id not in [r["chunk_id"] for r in out["results"]]  # dropped by the frozen resolution


def test_iterative_mode_is_the_phase_6_loop():
    _, service, server = setup()
    out = ok(server, "document_search", {"query": Q_CARD, "mode": "iterative", "max_rounds": 2})
    assert out["mode"] == "iterative" and 1 <= out["rounds"] <= 2
    assert service.requests[0].mode == "iterative" and service.requests[0].max_rounds == 2


def test_organization_filter_with_a_short_name():
    _, _, server = setup()
    out = ok(server, "document_search", {"query": Q_CARD, "organization": "Rutgers", "limit": 5})
    assert out["filters"]["organization"] == "Rutgers University"
    assert {r["document"]["organization"] for r in out["results"]} == {"Rutgers University"}
    assert [r["rank"] for r in out["results"]] == list(range(1, len(out["results"]) + 1))
    retrieval_ranks = [r["score"]["retrieval_rank"] for r in out["results"]]
    assert retrieval_ranks == sorted(retrieval_ranks)  # the retrieval's order, never re-ranked
    assert out["filtered_out"] == out["candidates"] - len(out["results"])


def test_version_filters_current_only_and_as_of():
    _, _, server = setup()
    q = "card charges not submitted suspended"
    cur = ok(server, "document_search", {"query": q, "current_only": True, "limit": 20})
    assert FEB_CARD.chunk_id not in {r["chunk_id"] for r in cur["results"]}
    assert JUL_CARD.chunk_id in {r["chunk_id"] for r in cur["results"]}
    march = ok(server, "document_search", {"query": q, "as_of": "2026-03-01", "limit": 20})
    ids = {r["chunk_id"] for r in march["results"]}
    assert FEB_CARD.chunk_id in ids and JUL_CARD.chunk_id not in ids
    assert {LODGE.chunk_id, ADVANCE.chunk_id} <= ids  # no effective date: kept, flagged undetermined
    assert march["filters"]["as_of"] == "2026-03-01"
    assert march["temporal"]["kind"] == "neutral"  # as_of is a metadata filter, not the query's temporal resolution
    august = ok(server, "document_search", {"query": q, "as_of": "2026-08-01", "doc_id": "proc-jul"})
    assert [r["chunk_id"] for r in august["results"]] == [JUL_CARD.chunk_id]


def standalone(cid, effective, superseded=None):
    """A document outside any version series, mentioning card charges so every query here retrieves it."""
    c = mk(cid, f"Card charges for {cid} trips are submitted after the trip.", doc=f"sa-{cid}", effective=effective)
    return c.model_copy(update={"superseded_date": superseded})


FUTURE = standalone("future", "2026-09-01")  # not yet effective on the March dates below
DATED = standalone("dated", "2025-06-01")  # in force since 2025
RETIRED = standalone("retired", "2025-01-01", superseded="2026-01-01")
MONTH_ONLY = standalone("monthonly", "2025-09")  # no full date: undetermined
UNDATED = standalone("undated", None)


def as_of_setup():
    chunks = CHUNKS + [FUTURE, DATED, RETIRED, MONTH_ONLY, UNDATED]
    stack = keyword_stack(*chunks)
    catalog = MetadataCatalog(stack.chunks, MANIFEST, CHUNK_MANIFEST, ALIASES)
    return catalog, build_server(Tools(catalog, LocalSearch(RetrievalService(stack, ALIASES))))


def by_doc(out):
    return {r["document"]["doc_id"]: r["as_of_applicability"] for r in out["results"]}


def test_as_of_excludes_a_future_dated_standalone_document():
    catalog, server = as_of_setup()
    march = by_doc(ok(server, "document_search", {"query": "card charges submitted", "as_of": "2026-03-15",
                                                   "limit": 20}))
    assert "sa-future" not in march and march["sa-dated"] == "in_force"
    october = by_doc(ok(server, "document_search", {"query": "card charges submitted", "as_of": "2026-10-01",
                                                     "limit": 20}))
    assert october["sa-future"] == "in_force"
    assert catalog.applicability(FUTURE, date(2026, 8, 31)) == "not_in_force"
    assert catalog.applicability(FUTURE, date(2026, 9, 1)) == "in_force"  # effective that day


def test_as_of_keeps_documents_without_a_usable_effective_date_and_flags_them():
    _, server = as_of_setup()
    out = ok(server, "document_search", {"query": "card charges submitted", "as_of": "2026-03-15", "limit": 20})
    got = by_doc(out)
    for doc in ("sa-undated", "sa-monthonly", "tu-lodging", "ru"):
        assert got[doc] == "undetermined"
    note = next(n for n in out["notes"] if n.startswith("as_of 2026-03-15"))
    assert "could not be established from metadata" in note
    for doc in ("sa-undated", "sa-monthonly", "tu-lodging", "ru"):
        assert doc in note
    assert "sa-dated" not in note and "proc-feb" not in note
    plain = ok(server, "document_search", {"query": "card charges submitted", "limit": 20})
    assert {r["as_of_applicability"] for r in plain["results"]} == {None}
    assert not any(n.startswith("as_of") for n in plain["notes"])


def test_as_of_excludes_a_superseded_standalone_document():
    catalog, server = as_of_setup()
    out = by_doc(ok(server, "document_search", {"query": "card charges submitted", "as_of": "2026-03-15",
                                                 "limit": 20}))
    assert "sa-retired" not in out
    assert catalog.applicability(RETIRED, date(2025, 12, 31)) == "in_force"
    assert catalog.applicability(RETIRED, date(2026, 1, 1)) == "not_in_force"  # superseded that day
    assert catalog.applicability(RETIRED, date(2024, 12, 31)) == "not_in_force"  # not yet effective
    assert catalog.applicability(standalone("gone", None, superseded="2026-01-01"), date(2026, 2, 1)) == \
        "not_in_force"  # a known superseded date excludes even without an effective date


@pytest.mark.parametrize("day, feb, jul", [
    (date(2026, 1, 31), "not_in_force", "not_in_force"),
    (date(2026, 2, 1), "in_force", "not_in_force"),
    (date(2026, 6, 30), "in_force", "not_in_force"),
    (date(2026, 7, 1), "not_in_force", "in_force"),
    (date(2026, 12, 31), "not_in_force", "in_force"),
])
def test_version_series_keep_the_version_registry_rule(day, feb, jul):
    catalog, _ = as_of_setup()
    assert (catalog.applicability(FEB_CARD, day), catalog.applicability(JUL_CARD, day)) == (feb, jul)
    in_force = {v.doc_id for v in catalog.registry.in_force(SERIES, day)}
    assert catalog.in_force(FEB_CARD, day) == ("proc-feb" in in_force)
    assert catalog.in_force(JUL_CARD, day) == ("proc-jul" in in_force)


def test_empty_results_are_results_not_errors():
    _, _, server = setup()
    out = ok(server, "document_search", {"query": Q_CARD, "doc_id": "proc-feb", "current_only": True})
    assert out["results"] == [] and out["filtered_out"] == out["candidates"] > 0
    assert "no retrieved chunk matched the filters" in out["notes"][0]
    meta = ok(server, "metadata_lookup", {"organization": "Rutgers", "series_id": SERIES})
    assert meta["documents"] == [] and meta["notes"] == ["no document matches every filter"]


def test_a_retrieval_without_candidates_is_reported_not_invented():
    class Empty:
        name = "empty"

        def search(self, req):
            return RetrievalResponse(**req.ids(), query=req.query, mode=req.mode, evidence=[],
                                     resolution={"kind": "neutral"})

    catalog = MetadataCatalog(CHUNKS, MANIFEST, CHUNK_MANIFEST, ALIASES)
    out = ok(build_server(Tools(catalog, Empty())), "document_search", {"query": "anything"})
    assert out["results"] == [] and out["candidates"] == 0 and out["notes"] == ["the retrieval returned no candidates"]


def test_unknown_organization_and_document_are_rejected_with_the_known_values():
    _, service, server = setup()
    e = error(server, "document_search", {"query": Q_CARD, "organization": "Harvard"})
    assert e["code"] == "unknown_organization"
    assert e["details"][0]["organizations"] == ["Rutgers University", "Test University"]
    e = error(server, "document_search", {"query": Q_CARD, "doc_id": "no-such-doc"})
    assert e["code"] == "unknown_document" and "proc-jul" in e["details"][0]["doc_ids"]
    assert service.requests == []  # rejected before any retrieval


# ---------------------------------------------------------------- metadata_lookup


def test_metadata_lookup_lists_the_documents_from_manifest_and_chunks():
    _, _, server = setup()
    out = ok(server, "metadata_lookup", {})
    assert [d["doc_id"] for d in out["documents"]] == ["proc-feb", "proc-jul", "ru", "tu-lodging"]
    assert out["corpus"] == {"documents": 4, "chunks": 5, "organizations": ["Rutgers University", "Test University"],
                             "ingestion_version": "ingest-test", "chunker_version": "chunk-test"}
    feb = out["documents"][0]
    assert feb["filename"] == "Procedures-FEB.pdf" and feb["page_count"] == 6 and feb["file_sha256"] == "f" * 64
    assert feb["effective_date"] == "2026-02-01" and feb["effective_date_text"] == "February 1, 2026"
    assert feb["superseded_date"] == "2026-07-01" and feb["is_current"] is False
    assert feb["series_versions"] == ["proc-feb", "proc-jul"] and feb["chunks"] == 1
    assert feb["missing_metadata"] == ["version"] and feb["provenance"] is None
    ru = next(d for d in out["documents"] if d["doc_id"] == "ru")
    assert ru["chunks"] == 2 and ru["excluded_records"] == 1 and ru["series_versions"] == []


def test_metadata_lookup_filters_and_provenance():
    _, _, server = setup()
    tu = ok(server, "metadata_lookup", {"organization": "tu", "current_only": True})
    assert tu["organization"] == "Test University"
    assert [d["doc_id"] for d in tu["documents"]] == ["proc-jul", "tu-lodging"]
    series = ok(server, "metadata_lookup", {"series_id": SERIES, "include_provenance": True})
    assert [d["doc_id"] for d in series["documents"]] == ["proc-feb", "proc-jul"]
    assert series["documents"][0]["provenance"] == MANIFEST["documents"][0]["metadata_provenance"]
    one = ok(server, "metadata_lookup", {"doc_id": "ru"})
    assert [d["doc_id"] for d in one["documents"]] == ["ru"]


def test_metadata_lookup_of_one_chunk():
    _, _, server = setup()
    out = ok(server, "metadata_lookup", {"chunk_id": ADVANCE.chunk_id, "include_text": True})
    c = out["chunk"]
    assert c["chunk_id"] == ADVANCE.chunk_id and c["text"] == ADVANCE.text and c["content_hash"] == ADVANCE.content_hash
    assert c["location"]["pages"] == [7] and c["location"]["clause_id"] == "11.3"
    assert [d["doc_id"] for d in out["documents"]] == ["ru"]
    assert ok(server, "metadata_lookup", {"chunk_id": ADVANCE.chunk_id})["chunk"]["text"] is None
    e = error(server, "metadata_lookup", {"chunk_id": "ru::nope"})
    assert e["code"] == "not_found"


# ---------------------------------------------------------------- malformed arguments and unknown tools


@pytest.mark.parametrize("name, arguments, loc", [
    ("document_search", {}, ["query"]),
    ("document_search", None, ["query"]),
    ("document_search", {"query": ""}, ["query"]),
    ("document_search", {"query": 42}, ["query"]),
    ("document_search", {"query": "q", "limit": 0}, ["limit"]),
    ("document_search", {"query": "q", "limit": 21}, ["limit"]),
    ("document_search", {"query": "q", "limit": "three"}, ["limit"]),
    ("document_search", {"query": "q", "mode": "deep"}, ["mode"]),
    ("document_search", {"query": "q", "as_of": "March 2026"}, ["as_of"]),
    ("document_search", {"query": "q", "top_k": 3}, ["top_k"]),
    ("document_search", {"query": "q", "session_id": "has spaces"}, ["session_id"]),
    ("document_search", {"query": "q", "current_only": True, "as_of": "2026-03-01"}, []),
    ("metadata_lookup", {"chunk_id": "x", "organization": "TU"}, []),
    ("metadata_lookup", {"organisation": "TU"}, ["organisation"]),
    ("database_query", {}, ["query_name"]),
    ("database_query", {"query_name": "drop_tables"}, ["query_name"]),
    ("database_query", {"query_name": "documents", "sql": "SELECT 1"}, ["sql"]),
    ("database_query", {"query_name": "documents", "parameters": {"owner": "x"}}, []),
    ("database_query", {"query_name": "document_versions"}, []),
    ("database_query", {"query_name": "documents", "limit": 501}, ["limit"]),
])
def test_malformed_arguments_are_rejected_before_any_work(name, arguments, loc):
    _, service, server = setup()
    e = error(server, name, arguments)
    assert e["code"] == "invalid_arguments" and e["details"]
    assert loc in [d["loc"] for d in e["details"]]
    assert service.requests == []


def test_unknown_tool_is_a_protocol_error():
    _, service, server = setup()
    with pytest.raises(MCPError) as info:
        call(server, "delete_everything", {"query": "x"})
    assert info.value.code == -32602 and "Unknown tool" in info.value.error.message
    assert info.value.error.data == {"tools": ["database_query", "document_search", "metadata_lookup"]}
    assert service.requests == []


def test_a_crash_is_reported_without_its_text():
    class Broken:
        name = "broken"

        def search(self, req):
            raise RuntimeError("secret internal detail")

    catalog = MetadataCatalog(CHUNKS, MANIFEST, CHUNK_MANIFEST, ALIASES)
    e = error(build_server(Tools(catalog, Broken())), "document_search", {"query": "q", "request_id": "r-crash"})
    assert e["code"] == "internal_error" and "secret" not in json.dumps(e)
    assert e["correlation"]["request_id"] == "r-crash"


# ---------------------------------------------------------------- database_query


def test_database_query_without_a_database_fails_honestly():
    _, _, server = setup()
    e = error(server, "database_query", {"query_name": "documents", "parameters": {"organization": "TU"},
                                         "session_id": "s-db"})
    assert e["code"] == "database_not_configured" and e["details"] == [{"adapter": "none"}]
    assert e["correlation"]["session_id"] == "s-db"


def test_database_adapter_contract():
    class Fake:
        name = "fake"

        def __init__(self):
            self.seen = []

        def execute(self, req, corr):
            self.seen.append((req, corr))
            rows = [{"doc_id": "proc-jul", "series_id": req.parameters["series_id"]}]
            return DatabaseQueryOutput(correlation=corr, query_name=req.query_name, source="fake",
                                       columns=["doc_id", "series_id"], rows=rows, row_count=1, truncated=False)

    db = Fake()
    _, _, server = setup(database=db)
    out = ok(server, "database_query", {"query_name": "document_versions", "parameters": {"series_id": SERIES},
                                        "limit": 10, "request_id": "r-db"})
    assert out["rows"] == [{"doc_id": "proc-jul", "series_id": SERIES}] and out["source"] == "fake"
    assert out["correlation"]["request_id"] == "r-db"
    req, corr = db.seen[0]
    assert req.limit == 10 and corr.request_id == "r-db"


# ---------------------------------------------------------------- correlation


def test_correlation_ids_are_echoed_and_reach_the_retrieval():
    _, service, server = setup()
    ids = {"request_id": "r-1", "job_id": "j-1", "session_id": "s-1"}
    out = ok(server, "document_search", {"query": Q_CARD, **ids})
    assert out["correlation"] == ids
    assert service.requests[0].ids() == ids
    assert ok(server, "metadata_lookup", {"doc_id": "ru", **ids})["correlation"] == ids


def test_a_request_id_is_made_when_none_is_given_and_nothing_leaks_between_calls():
    _, service, server = setup()
    ok(server, "document_search", {"query": Q_CARD, "session_id": "s-a"})
    second = ok(server, "document_search", {"query": Q_CARD})
    first_req, second_req = service.requests
    assert first_req.session_id == "s-a" and first_req.request_id.startswith("mcp-")
    assert second_req.session_id is None and second_req.job_id is None
    assert second["correlation"]["request_id"].startswith("mcp-")
    assert second_req.request_id != first_req.request_id
    assert correlation.current() == {"request_id": None, "job_id": None, "session_id": None}


# ---------------------------------------------------------------- the phase 7 retrieval service


def test_remote_backend_goes_through_the_retrieval_service_with_the_ids():
    stack, service, local_server = setup()
    http = TestClient(retrieval_app(service))
    remote = build_server(Tools(MetadataCatalog(stack.chunks, MANIFEST, CHUNK_MANIFEST, ALIASES), RemoteSearch(http)))
    args = {"query": Q_FEB, "limit": 5, "session_id": "s-r", "job_id": "j-r", "request_id": "r-r"}
    via_service = ok(remote, "document_search", args)
    local = ok(local_server, "document_search", args)
    assert via_service["backend"].startswith("remote ")
    assert {k: v for k, v in via_service.items() if k != "backend"} == {k: v for k, v in local.items() if k != "backend"}
    assert [r.ids() for r in service.requests] == [{"request_id": "r-r", "job_id": "j-r", "session_id": "s-r"}] * 2


def test_retrieval_service_failures_are_backend_unavailable():
    stack = keyword_stack(*CHUNKS)
    catalog = MetadataCatalog(stack.chunks, MANIFEST, CHUNK_MANIFEST, ALIASES)

    def wrong_ids(request: httpx.Request):
        body = json.loads(request.content)
        resp = RetrievalService(stack, ALIASES).handle(RetrievalRequest(**{**body, "request_id": "other"}))
        return httpx.Response(200, content=resp.encode())

    for handler, text in [(lambda r: httpx.Response(500, text="boom"), "HTTP 500"),
                          (wrong_ids, "correlation ids")]:
        http = httpx.Client(base_url="http://retrieval.test", transport=httpx.MockTransport(handler))
        e = error(build_server(Tools(catalog, RemoteSearch(http))), "document_search", {"query": "q"})
        assert e["code"] == "backend_unavailable" and text in e["message"]


# ---------------------------------------------------------------- isolation, network, models


FROZEN = ["retrieval", "generation", "temporal", "chunking", "ingestion", "adaptive"]


def test_frozen_core_and_phase_7_services_do_not_depend_on_mcp():
    for package in FROZEN + ["services"]:
        for path in (ROOT / package).rglob("*.py"):
            if "mcp" in path.relative_to(ROOT).parts:
                continue
            source = path.read_text(encoding="utf-8")
            assert "services.mcp" not in source and "import mcp" not in source and "from mcp" not in source, path


def test_the_tools_do_not_change_the_core_state_or_results():
    stack, _, server = setup()
    before_chunks = copy.deepcopy(stack.chunks)
    before = [(e.chunk.chunk_id, e.score) for e in retrieve(stack, Q_FEB).evidence]
    ok(server, "document_search", {"query": Q_FEB, "organization": "TU", "as_of": "2026-03-01"})
    ok(server, "metadata_lookup", {"include_provenance": True})
    assert stack.chunks == before_chunks
    assert [(e.chunk.chunk_id, e.score) for e in retrieve(stack, Q_FEB).evidence] == before


def test_no_network_and_no_model_calls(offline):
    _, _, server = setup()
    assert ok(server, "document_search", {"query": Q_CARD})["results"]
    assert ok(server, "document_search", {"query": Q_CARD, "mode": "iterative"})["results"]
    assert ok(server, "metadata_lookup", {})["documents"]
    assert error(server, "database_query", {"query_name": "documents"})["code"] == "database_not_configured"
    with pytest.raises(AssertionError, match="network access"):
        socket.socket().connect(("127.0.0.1", 9))


# ---------------------------------------------------------------- the real repository metadata and transport


needs_data = pytest.mark.skipif(not (ROOT / "data" / "ingested" / "_manifest.json").exists(), reason="no data/")


@needs_data
def test_real_metadata_catalog_matches_the_repository_files():
    from chunking.pipeline import load_chunks, load_manifest

    catalog = MetadataCatalog.load()
    ingested = json.loads((ROOT / "data" / "ingested" / "_manifest.json").read_text(encoding="utf-8"))
    chunk_counts = {d: len(cs) for d, cs in load_chunks().items()}
    excluded = {d["doc_id"]: d["excluded_records"] for d in load_manifest()["documents"]}
    assert catalog.doc_ids() == sorted(d["doc_id"] for d in ingested["documents"])
    for m in ingested["documents"]:
        d = catalog.document(m["doc_id"], include_provenance=True)
        assert (d.filename, d.title, d.organization, d.page_count, d.file_sha256) == (
            m["filename"], m["title"], m["organization"], m["page_count"], m["file_sha256"])
        assert d.effective_date_text == m["effective_date"] and d.revision_date == m["revision_date"]
        assert d.chunks == chunk_counts[m["doc_id"]] and d.excluded_records == excluded[m["doc_id"]]
        assert d.provenance == m["metadata_provenance"]
    uconn = catalog.resolve_organization("UConn")
    assert uconn == "University of Connecticut"
    proc = [d for d in catalog.doc_ids() if catalog.document(d).series_id]
    assert [catalog.document(d).effective_date for d in catalog.series_versions(proc[0])] == ["2026-02-01", "2026-07-01"]


@needs_data
def test_stdio_server_process_discovery_and_metadata_lookup():
    """The real server process over stdio (python -m services.mcp --transport stdio). The remote retrieval backend is
    configured but not called; only discovery and the metadata tool run."""
    from mcp.client.stdio import StdioServerParameters

    params = StdioServerParameters(command=sys.executable, cwd=str(ROOT),
                                   args=["-m", "services.mcp", "--transport", "stdio", "--retrieval", "remote"])

    async def go():
        async with Client(params) as client:
            tools = [t.name for t in (await client.list_tools()).tools]
            r = await client.call_tool("metadata_lookup", {"organization": "UConn", "session_id": "s-stdio"})
            return tools, r

    tools, r = anyio.run(go)
    assert tools == ["document_search", "metadata_lookup", "database_query"]
    assert not r.is_error and r.structured_content["correlation"]["session_id"] == "s-stdio"
    assert {d["organization"] for d in r.structured_content["documents"]} == {"University of Connecticut"}
    assert len(r.structured_content["documents"]) == 3
