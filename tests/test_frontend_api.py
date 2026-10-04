"""The api additions the frontend reads (phase 10B, offline): GET /sessions/{session_id} and the `traceresponse`
header. Both only expose what already exists: the stored session and the ids of a recorded span. The in-process rig
of tests/test_services.py; no network, no models.
"""

import json
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app as api_app
from services.contracts import QueryResponse
from services.retrieval.app import create_app as retrieval_app
from services.telemetry.testing import capture
from tests.test_services import CONVERSATION, Rig, session_setup
from tests.test_session import ALIASES, Q_FEB, Q_TWO

IDS = ("request_id", "job_id", "session_id")


def rig(mode="single"):
    stack, provider = session_setup()
    return Rig(stack, provider, ALIASES, mode)


# ---------------------------------------------------------------- GET /sessions/{session_id}


def test_an_unknown_session_is_404_and_a_malformed_id_is_422():
    r = rig()
    assert r.api.get("/sessions/nobody").status_code == 404
    assert "nobody" in r.api.get("/sessions/nobody").json()["detail"]
    assert r.api.get("/sessions/bad id!").status_code == 422
    assert r.api.get("/sessions/" + "x" * 129).status_code == 422
    assert r.broker.published == []  # reading a session queues nothing


def test_the_session_endpoint_returns_exactly_what_the_orchestrator_stored():
    r = rig()
    for question in CONVERSATION:
        assert r.query("conv", question).status_code == 200
    body = r.api.get("/sessions/conv").json()
    assert (body["schema_version"], body["session_id"]) == (1, "conv")
    assert body["turns"] == json.loads(json.dumps(r.store.get("conv").to_dict()))["turns"]  # the same, as JSON
    assert [t["resolution"]["kind"] for t in body["turns"]] == ["self_contained", "follow_up", "self_contained",
                                                                 "unresolved"]
    follow_up, unresolved = body["turns"][1], body["turns"][3]
    assert follow_up["resolution"]["anchor"] == 0 and follow_up["resolution"]["query"] != follow_up["question"]
    assert unresolved["answer"] is None and unresolved["status"] == "abstained"


def test_the_session_endpoint_exposes_the_multi_intent_detail_the_query_response_summarises():
    r = rig()
    summary = QueryResponse.model_validate(r.query("s1", Q_TWO).json())
    answer = r.api.get("/sessions/s1").json()["turns"][0]["answer"]
    assert answer["decomposition"]["multi"] is True and len(answer["decomposition"]["intents"]) == 2
    assert [i["sub_query"] for i in answer["intents"]] == [i["sub_query"] for i in answer["decomposition"]["intents"]]
    assert all({"status", "retrieved", "organizations", "citations"} <= set(i) for i in answer["intents"])
    assert answer["claims"] and all({"text", "citations", "quotes", "intents"} <= set(c) for c in answer["claims"])
    assert set(answer["evidence_intents"]) >= set(summary.evidence)
    # the summary is the same turn: nothing in the endpoint contradicts POST /query
    assert (answer["strategy"], answer["status"], answer["text"]) == (summary.strategy, summary.answer_status,
                                                                      summary.text)
    assert [c["chunk_id"] for c in answer["citations"]] == [c["chunk_id"] for c in summary.citations]


def test_reading_a_session_changes_nothing():
    r = rig("iterative")
    first = r.query("s1", Q_FEB).json()
    before, calls, published = asdict(r.store.get("s1")), list(r.generation.requests), list(r.broker.published)
    for _ in range(3):
        assert r.api.get("/sessions/s1").status_code == 200
    assert asdict(r.store.get("s1")) == before and r.generation.requests == calls and r.broker.published == published
    again = r.query("s1", "Does that apply currently?").json()  # the conversation goes on as before
    assert (again["turn_index"], again["resolution"]["anchor"]) == (1, first["turn_index"])


def test_the_query_contract_is_unchanged_by_the_new_route():
    r = rig()
    body = r.query("s1", Q_FEB, request_id="client-1").json()
    assert set(body) == set(QueryResponse.model_fields)
    spec = r.api.get("/openapi.json").json()
    assert set(spec["paths"]) == {"/health", "/query", "/sessions/{session_id}"}
    assert spec["paths"]["/query"]["post"]["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/QueryRequest"}


def test_the_api_only_delegates_session_reads():
    class Fake:
        def session(self, session_id):
            return {"turns": [{"index": 0}]} if session_id == "known" else None

    client = TestClient(api_app(Fake()))
    assert client.get("/sessions/known").json() == {"schema_version": 1, "session_id": "known", "turns": [{"index": 0}]}
    assert client.get("/sessions/other").status_code == 404


# ---------------------------------------------------------------- traceresponse


def test_no_traceresponse_header_without_tracing():
    r = rig()
    assert "traceresponse" not in r.query("s1", Q_FEB).headers
    assert "traceresponse" not in r.api.get("/sessions/s1").headers
    assert "traceresponse" not in r.api.get("/health").headers
    # a caller's traceparent must not be echoed back as if this service had recorded a span of its own
    caller = {"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}
    assert "traceresponse" not in r.api.post("/query", json={"session_id": "s1", "question": Q_FEB},
                                             headers=caller).headers
    assert "traceresponse" not in r.api.get("/sessions/s1", headers=caller).headers
    assert "traceresponse" not in TestClient(retrieval_app(r.retrieval)).post(
        "/retrieve", json={"query": Q_FEB}, headers=caller).headers


def test_a_span_reports_ids_only_when_it_records():
    from services import telemetry

    with telemetry.span("retrieval.execute", telemetry.RETRIEVAL) as off:
        assert (off.trace_id, off.span_id) == (None, None)
    with capture():
        with telemetry.span("retrieval.execute", telemetry.RETRIEVAL) as on:
            assert len(on.trace_id) == 32 and len(on.span_id) == 16


def test_traceresponse_names_the_requests_own_server_span():
    r = rig()
    with capture() as spans:
        parent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        joined = r.api.post("/query", json={"session_id": "s1", "question": Q_FEB}, headers={"traceparent": parent})
        own = r.api.get("/sessions/s1")
        missing = r.api.get("/sessions/nobody")
        health = r.api.get("/health")
        retrieve = TestClient(retrieval_app(r.retrieval)).post("/retrieve", json={"query": Q_FEB})
    finished = spans.get_finished_spans()

    def named(name):
        return [s for s in finished if s.name == name]

    def header_of(span):
        return f"00-{span.context.trace_id:032x}-{span.context.span_id:016x}-01"

    assert joined.headers["traceresponse"] == header_of(named("POST /query")[0])
    assert joined.headers["traceresponse"].startswith("00-0af7651916cd43dd8448eb211c80319c-")  # the caller's trace
    found, not_found = named("GET /sessions/{session_id}")
    assert own.headers["traceresponse"] == header_of(found)
    assert missing.status_code == 404 and missing.headers["traceresponse"] == header_of(not_found)
    assert found.context.trace_id != not_found.context.trace_id  # each request without a traceparent: its own trace
    assert (found.attributes["adaptiverag.session_id"], not_found.attributes["adaptiverag.session_id"]) == (
        "s1", "nobody")
    assert "traceresponse" not in health.headers  # GET /health is not traced
    assert retrieve.headers["traceresponse"] == header_of(named("POST /retrieve")[-1])
    assert Q_FEB not in str([dict(s.attributes) for s in finished])


@pytest.mark.parametrize("mode", ["single", "iterative"])
def test_the_header_does_not_change_a_response_body(mode):
    plain, traced = rig(mode), rig(mode)
    want = [plain.query("s", q).json() for q in CONVERSATION]
    with capture():
        got = [traced.query("s", q).json() for q in CONVERSATION]

    def comparable(body):
        return {k: v for k, v in body.items() if k not in IDS}

    assert [comparable(b) for b in got] == [comparable(b) for b in want]
    assert traced.api.get("/sessions/s").json()["turns"] == plain.api.get("/sessions/s").json()["turns"]
