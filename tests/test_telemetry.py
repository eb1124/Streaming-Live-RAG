"""Tracing (phase 10A, offline): the tracing foundation, the span of every service boundary, the trace tree of a
query, correlation ids on spans, failures, and above all that tracing changes nothing and records no payload.

Spans are read from the OpenTelemetry SDK's in-memory exporter (services.telemetry.testing.capture): no collector,
no network, no database, no queue. The services are the in-process rig of tests/test_services.py and the MCP server
of tests/test_mcp.py, with their keyword stand-ins and stub models.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp.shared.exceptions import MCPError

pytest.importorskip("opentelemetry.sdk.trace")

from opentelemetry.trace import SpanKind, StatusCode  # noqa: E402

from adaptive.streaming.controller import IterativeRetriever  # noqa: E402
from services import correlation, telemetry  # noqa: E402
from services.api.app import create_app as api_app  # noqa: E402
from services.contracts import GenerationRequest, RetrievalRequest  # noqa: E402
from services.generation.service import GenerationService  # noqa: E402
from services.mcp.backends import LocalSearch, RemoteSearch  # noqa: E402
from services.mcp.catalog import MetadataCatalog  # noqa: E402
from services.mcp.server import build_server  # noqa: E402
from services.mcp.tools import Tools  # noqa: E402
from services.retrieval.app import create_app as retrieval_app  # noqa: E402
from services.retrieval.rounds import TracedIterativeRetriever  # noqa: E402
from services.retrieval.service import RetrievalService  # noqa: E402
from services.telemetry import provider as telemetry_provider  # noqa: E402
from services.telemetry.config import TelemetrySettings  # noqa: E402
from services.telemetry.spans import MAX_TEXT, safe_attributes  # noqa: E402
from services.telemetry.testing import capture  # noqa: E402
from tests.test_mcp import (ALIASES as MCP_ALIASES, CHUNK_MANIFEST, CHUNKS as MCP_CHUNKS, MANIFEST, Q_CARD,  # noqa: E402
                            call)
from tests.test_multi_intent_controller import stack as keyword_stack  # noqa: E402
from tests.test_services import Rig, session_setup  # noqa: E402
from tests.test_session import ALIASES as SESSION_ALIASES, CHUNKS as SESSION_CHUNKS, Q_FEB, Q_TWO  # noqa: E402
from tests.test_streaming import ALIASES as STREAM_ALIASES, QA, QB, WITH_MILEAGE, make_stack, provider_for  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OTEL_VARIABLES = ("OTEL_SDK_DISABLED", "OTEL_TRACES_EXPORTER", "OTEL_SERVICE_NAME", "OTEL_EXPORTER_OTLP_ENDPOINT",
                  "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
ROUTES = ("POST /query", "POST /retrieve", "POST /generate")


@pytest.fixture
def spans():
    with capture() as exporter:
        yield exporter


@pytest.fixture
def unconfigured(monkeypatch):
    """No OTEL_* variable and no configured provider, before and after the test."""
    for name in OTEL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    telemetry.shutdown()
    yield
    telemetry.shutdown()


def named(exporter, name):
    return [s for s in exporter.get_finished_spans() if s.name == name]


def one(exporter, name):
    found = named(exporter, name)
    assert len(found) == 1, [s.name for s in exporter.get_finished_spans()]
    return found[0]


def ancestors(exporter, span):
    """The span's parents, nearest first, following the actual span ids."""
    by_id = {s.context.span_id: s for s in exporter.get_finished_spans()}
    out = []
    while span.parent is not None and span.parent.span_id in by_id:
        span = by_id[span.parent.span_id]
        out.append(span)
    return out


def is_error(span, kind=None):
    return span.status.status_code is StatusCode.ERROR and (kind is None or span.attributes["error.type"] == kind)


def dump(exporter) -> str:
    """Everything the exporter received, as text: names, attributes, events, statuses, resources."""
    return json.dumps([{"name": s.name, "attributes": dict(s.attributes), "status": s.status.description,
                        "events": [{"name": e.name, "attributes": dict(e.attributes)} for e in s.events],
                        "resource": dict(s.resource.attributes)} for s in exporter.get_finished_spans()], default=str)


def stream_rig(mode="iterative"):
    return Rig(make_stack(WITH_MILEAGE), provider_for(WITH_MILEAGE), STREAM_ALIASES, mode)


def mcp_server(search="local", database=None):
    stack = keyword_stack(*MCP_CHUNKS)
    service = RetrievalService(stack, MCP_ALIASES)
    backend = LocalSearch(service) if search == "local" else RemoteSearch(TestClient(retrieval_app(service)))
    return build_server(Tools(MetadataCatalog(stack.chunks, MANIFEST, CHUNK_MANIFEST, MCP_ALIASES), backend, database))


# ---------------------------------------------------------------- the foundation: settings, provider, helpers


def test_settings_default_to_no_exporter_and_no_collector(unconfigured, monkeypatch):
    s = TelemetrySettings.from_env("adaptiverag-api")
    assert (s.service_name, s.exporter, s.disabled, s.enabled) == ("adaptiverag-api", "none", False, False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.invalid:4318")
    assert TelemetrySettings.from_env("x").exporter == "otlp"  # an endpoint alone turns the export on
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "Console")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "my-api")
    s = TelemetrySettings.from_env("adaptiverag-api")
    assert (s.service_name, s.exporter, s.enabled) == ("my-api", "console", True)
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert TelemetrySettings.from_env("x").enabled is False
    with pytest.raises(ValueError, match="OTEL_TRACES_EXPORTER"):
        TelemetrySettings(service_name="x", exporter="zipkin")


def test_without_configuration_tracing_is_a_no_op(unconfigured):
    assert telemetry.configure(telemetry.API) is False and telemetry.active() is False
    with telemetry.span("orchestrator.execute", telemetry.ORCHESTRATOR) as sp:
        sp.set({"job.status": "completed"})
        sp.error("X")
        assert (sp.trace_id, sp.span_id) == (None, None) and telemetry.trace_headers() == {}
    telemetry.annotate({"job.status": "completed"}, ids={"job_id": "j"})


def test_configure_is_idempotent(unconfigured):
    console = TelemetrySettings(service_name="adaptiverag-api", exporter="console")
    assert telemetry.configure(telemetry.API, console) is True and telemetry.active()
    first = telemetry_provider._provider
    assert first.resource.attributes["service.name"] == "adaptiverag-api"
    other = TelemetrySettings(service_name="someone-else", exporter="console")
    assert telemetry.configure("someone-else", other) is True
    assert telemetry_provider._provider is first  # the second call configured nothing
    assert len(first._active_span_processor._span_processors) == 1  # one exporter, not two
    telemetry.shutdown()
    assert telemetry.active() is False and telemetry.configure(telemetry.API) is False  # off again by default


def test_a_broken_configuration_leaves_tracing_off_and_does_not_raise(unconfigured, monkeypatch):
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "zipkin")
    assert telemetry.configure(telemetry.API) is False and telemetry.active() is False
    telemetry.shutdown()

    def no_sdk(settings):
        raise ImportError("No module named 'opentelemetry.sdk'")

    monkeypatch.setattr(telemetry_provider, "_build", no_sdk)
    assert telemetry.configure(telemetry.API, TelemetrySettings("x", "console")) is False


def test_otlp_export_is_configured_from_the_environment_without_connecting(unconfigured, monkeypatch):
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.invalid:4318")
    assert telemetry.configure(telemetry.RETRIEVAL) is True
    processor = telemetry_provider._provider._active_span_processor._span_processors[0]
    assert processor.span_exporter._endpoint == "http://collector.invalid:4318/v1/traces"  # nothing is sent: no span


def test_captures_are_isolated_and_restore_the_provider(unconfigured):
    with capture() as first:
        with telemetry.span("retrieval.execute", telemetry.RETRIEVAL):
            pass
        assert telemetry.active()
    with capture() as second:
        with telemetry.span("generation.execute", telemetry.GENERATION):
            pass
    assert [s.name for s in first.get_finished_spans()] == ["retrieval.execute"]
    assert [s.name for s in second.get_finished_spans()] == ["generation.execute"]
    assert telemetry.active() is False and telemetry_provider._provider is None


def test_only_allow_listed_scalar_attributes_are_set(spans):
    assert safe_attributes({"retrieval.mode": "single", "query": "what?", "retrieval.k": 10, "job.status": None,
                            "retrieval.rounds": [1, 2], "generation.model": "m" * 1000}) == {
        "retrieval.mode": "single", "retrieval.k": 10, "generation.model": "m" * MAX_TEXT}
    with telemetry.span("retrieval.execute", telemetry.RETRIEVAL, {"retrieval.mode": "single", "question": "q"}) as sp:
        sp.set({"answer": "a", "retrieval.evidence_count": 3})
        assert len(sp.trace_id) == 32 and len(sp.span_id) == 16
    s = one(spans, "retrieval.execute")
    assert dict(s.attributes) == {"adaptiverag.service": "adaptiverag-retrieval",
                                  "adaptiverag.operation": "retrieval.execute", "retrieval.mode": "single",
                                  "retrieval.evidence_count": 3}


def test_spans_nest_and_carry_the_bound_correlation_ids(spans):
    with correlation.bind(job_id="j-1", session_id="s-1"):
        with telemetry.span("orchestrator.execute", telemetry.ORCHESTRATOR):
            with telemetry.span("retrieval.execute", telemetry.RETRIEVAL, ids={"request_id": "r-1"}):
                pass
    outer, inner = one(spans, "orchestrator.execute"), one(spans, "retrieval.execute")
    assert inner.parent.span_id == outer.context.span_id and inner.context.trace_id == outer.context.trace_id
    assert outer.attributes["correlation_id"] == "j-1" and outer.attributes["adaptiverag.session_id"] == "s-1"
    assert "adaptiverag.request_id" not in outer.attributes
    assert inner.attributes["adaptiverag.request_id"] == "r-1" and inner.attributes["correlation_id"] == "j-1"
    with telemetry.span("mcp.tool.execute", telemetry.MCP, ids={"request_id": "r-2"}):
        pass
    assert one(spans, "mcp.tool.execute").attributes["correlation_id"] == "r-2"  # no job: the request id
    assert correlation.current() == {"request_id": None, "job_id": None, "session_id": None}


def test_an_exception_is_recorded_and_re_raised_unchanged(spans):
    boom = KeyError("SECRET-PAYLOAD")
    with pytest.raises(KeyError) as info:
        with telemetry.span("generation.execute", telemetry.GENERATION):
            raise boom
    assert info.value is boom
    s = one(spans, "generation.execute")
    assert is_error(s, "KeyError") and s.status.description == "KeyError"
    assert [(e.name, dict(e.attributes)) for e in s.events] == [("exception", {"exception.type": "builtins.KeyError"})]
    assert "SECRET-PAYLOAD" not in dump(spans)


# ---------------------------------------------------------------- HTTP spans


def test_an_http_request_is_one_server_span():
    stack, provider = session_setup()
    rig = Rig(stack, provider, SESSION_ALIASES)
    with capture() as spans:
        r = rig.query("s1", Q_FEB, request_id="client-1")
        assert rig.api.get("/health").status_code == 200  # not traced
    assert r.status_code == 200
    s = one(spans, "POST /query")
    assert s.kind is SpanKind.SERVER and s.parent is None and s.status.status_code is StatusCode.UNSET
    assert dict(s.attributes) == {
        "adaptiverag.service": "adaptiverag-api", "adaptiverag.operation": "POST /query",
        "http.request.method": "POST", "http.route": "/query", "http.response.status_code": 200,
        "correlation_id": r.json()["job_id"], "adaptiverag.request_id": "client-1",
        "adaptiverag.job_id": r.json()["job_id"], "adaptiverag.session_id": "s1"}
    assert not [x for x in spans.get_finished_spans() if "/health" in x.name]


def test_http_status_codes_and_unmatched_paths(spans):
    stack, provider = session_setup()
    assert Rig(stack, provider, SESSION_ALIASES).query("bad id!", "q").status_code == 422
    invalid = one(spans, "POST /query")
    assert invalid.attributes["http.response.status_code"] == 422 and not is_error(invalid)  # the client's error
    spans.clear()
    failing = Rig(stack, provider, SESSION_ALIASES, broken_retrieval=True)
    assert failing.query("s1", Q_FEB).status_code == 502
    assert failing.api.get("/no/such/path/SECRET-PATH?token=SECRET-TOKEN").status_code == 404
    failed = one(spans, "POST /query")
    assert failed.attributes["http.response.status_code"] == 502 and is_error(failed, "502")
    unmatched = one(spans, "GET")  # no route: the method only, never the raw URL
    assert "http.route" not in unmatched.attributes and "SECRET" not in dump(spans)


def test_an_unhandled_exception_is_an_error_span_and_still_a_500(spans):
    class Crashing:
        def query(self, req):
            raise RuntimeError("SECRET-CRASH")

    r = TestClient(api_app(Crashing()), raise_server_exceptions=False).post(
        "/query", json={"session_id": "s1", "question": "q"})
    assert r.status_code == 500
    s = one(spans, "POST /query")
    assert is_error(s, "RuntimeError") and s.attributes["http.response.status_code"] == 500
    assert "SECRET-CRASH" not in dump(spans)


def test_trace_context_crosses_http_and_only_then(spans):
    assert telemetry.trace_headers() == {}  # nothing is current: no header is sent
    stack, provider = session_setup()
    rig = Rig(stack, provider, SESSION_ALIASES)
    parent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    assert rig.api.post("/query", json={"session_id": "s1", "question": Q_FEB},
                        headers={"traceparent": parent}).status_code == 200
    s = one(spans, "POST /query")
    assert f"{s.context.trace_id:032x}" == "0af7651916cd43dd8448eb211c80319c"
    assert f"{s.parent.span_id:016x}" == "b7ad6b7169203331" and s.parent.is_remote
    assert {x.context.trace_id for x in spans.get_finished_spans()} == {s.context.trace_id}


# ---------------------------------------------------------------- the trace tree of a query


def test_the_trace_tree_of_a_query_through_the_services(spans):
    """POST /query > orchestrator.execute > { POST /retrieve > retrieval.execute > retrieval.round * n,
    POST /generate > generation.execute }, in one trace, every parent taken from the actual span ids."""
    rig = stream_rig()
    body = rig.query("conv-1", QB).json()
    assert body["status"] == "completed"
    finished = spans.get_finished_spans()
    assert {s.context.trace_id for s in finished} == {finished[0].context.trace_id}  # one trace

    api, orchestrator = one(spans, "POST /query"), one(spans, "orchestrator.execute")
    assert api.parent is None and orchestrator.parent.span_id == api.context.span_id

    retrievals, rounds = named(spans, "retrieval.execute"), named(spans, "retrieval.round")
    assert len(retrievals) == len(rig.retrieval.requests) >= 1
    for retrieval, request in zip(retrievals, rig.retrieval.requests):  # spans end in request order
        http, parent = ancestors(spans, retrieval)[:2]
        assert (http.name, http.kind, parent) == ("POST /retrieve", SpanKind.SERVER, orchestrator)
        mine = [r for r in rounds if r.parent.span_id == retrieval.context.span_id]
        want = IterativeRetriever(aliases=STREAM_ALIASES)(rig.retrieval.stack, request.query, request.k).trace
        assert [r.attributes["retrieval.round.number"] for r in mine] == list(range(1, want.iterations + 1))
        assert retrieval.attributes["retrieval.rounds"] == want.iterations
        assert retrieval.attributes["retrieval.stop_reason"] == want.stop_reason
        for r in mine:  # a round lies inside its retrieval
            assert retrieval.start_time <= r.start_time <= r.end_time <= retrieval.end_time
    assert len(rounds) == sum(r.attributes["retrieval.rounds"] for r in retrievals)  # every round has a parent
    assert max(r.attributes["retrieval.rounds"] for r in retrievals) >= 2  # the loop did refine

    generations = named(spans, "generation.execute")
    assert len(generations) == len(rig.generation.requests) >= 1
    for generation in generations:
        http, parent = ancestors(spans, generation)[:2]
        assert (http.name, http.kind, parent) == ("POST /generate", SpanKind.SERVER, orchestrator)
    assert {s.name for s in finished} == {"POST /query", "orchestrator.execute", "POST /retrieve",
                                          "retrieval.execute", "retrieval.round", "POST /generate",
                                          "generation.execute"}


def test_every_span_of_a_query_carries_its_correlation_ids(spans):
    rig = stream_rig()
    body = rig.query("conv-7", QB, request_id="req-42").json()
    for s in spans.get_finished_spans():
        assert s.attributes["correlation_id"] == body["job_id"], s.name
        assert s.attributes["adaptiverag.job_id"] == body["job_id"], s.name
        assert s.attributes["adaptiverag.session_id"] == "conv-7", s.name
    assert one(spans, "POST /query").attributes["adaptiverag.request_id"] == "req-42"
    assert correlation.current() == {"request_id": None, "job_id": None, "session_id": None}  # nothing leaks


def test_orchestrator_span():
    stack, provider = session_setup()
    rig = Rig(stack, provider, SESSION_ALIASES)
    with capture() as spans:
        first = rig.query("s1", Q_FEB).json()
        rig.query("s1", "Does that apply currently?")
    first_span, second_span = named(spans, "orchestrator.execute")
    assert {k: v for k, v in first_span.attributes.items() if not k.startswith(("adaptiverag.", "correlation"))} == {
        "job.status": "completed", "session.turn_index": 0, "session.continued": False,
        "orchestrator.resolution": "self_contained", "orchestrator.answer_status": "answered",
        "orchestrator.citation_count": len(first["citations"]),
        # the decision and the lineage of the answer, from the stored turn
        "orchestrator.decision": "retrieve", "orchestrator.retrieval_required": True,
        "orchestrator.strategy": "delegate", "orchestrator.claim_count": 1, "orchestrator.answer_version": 1}
    assert first_span.attributes["adaptiverag.service"] == "adaptiverag-orchestrator"
    assert (second_span.attributes["session.turn_index"], second_span.attributes["session.continued"],
            second_span.attributes["orchestrator.resolution"]) == (1, True, "follow_up")
    assert second_span.attributes["session.anchor_turn_index"] == 0  # the turn the follow-up was resolved against


def test_orchestrator_span_records_the_decision_and_the_answer_version_of_every_kind_of_turn():
    from tests.test_session_refine import CHUNKS as REFINE_CHUNKS, LATE, Q1, model
    from tests.test_multi_intent_controller import stack as keyword_stack
    from tests.test_session import ALIASES as REFINE_ALIASES
    from generation.providers import StubProvider

    rig = Rig(keyword_stack(*REFINE_CHUNKS), StubProvider(model), REFINE_ALIASES)
    turns = [Q1, LATE, "Give me that in two bullet points.", "I meant a student trip.", "Thanks, that's all.",
             "What about the..."]
    with capture() as spans:
        bodies = [rig.query("s", q).json() for q in turns]
    got = [{k: s.attributes.get(k) for k in ("orchestrator.resolution", "orchestrator.decision",
                                             "orchestrator.retrieval_required", "session.anchor_turn_index",
                                             "orchestrator.strategy", "orchestrator.answer_version")}
           for s in named(spans, "orchestrator.execute")]
    assert got == [
        dict(zip(got[0], ("self_contained", "retrieve", True, None, "delegate", 1))),
        dict(zip(got[0], ("refinement", "refine", True, 0, "refined", 2))),  # answer v2 of the question of turn 0
        dict(zip(got[0], ("presentation", "presentation", False, 1, "refined", 2))),  # the same answer, laid out again
        dict(zip(got[0], ("refinement", "refine", True, 1, "refined", 3))),
        dict(zip(got[0], ("suppress", "suppress", False, 3, None, None))),
        dict(zip(got[0], ("wait", "wait", False, 3, None, None)))]
    # the citations of a turn are found from its span: session id and turn index name the stored turn
    stored = rig.api.get("/sessions/s").json()["turns"]
    for span, body in zip(named(spans, "orchestrator.execute"), bodies):
        turn = stored[span.attributes["session.turn_index"]]
        assert span.attributes["adaptiverag.session_id"] == "s" and span.attributes["correlation_id"] == body["job_id"]
        assert [c["chunk_id"] for c in (turn["answer"] or {}).get("citations", [])] == [c["chunk_id"] for c in body["citations"]]
        assert span.attributes["orchestrator.citation_count"] == len(body["citations"])
        assert span.start_time and span.end_time >= span.start_time  # execution timestamps


# ---------------------------------------------------------------- retrieval and its rounds


def test_retrieval_spans_single_mode(spans):
    stack, _ = session_setup()
    req = RetrievalRequest(query=Q_FEB, job_id="j-1", session_id="s-1")
    resp = RetrievalService(stack, SESSION_ALIASES).handle(req)
    s = one(spans, "retrieval.execute")
    assert dict(s.attributes) == {
        "adaptiverag.service": "adaptiverag-retrieval", "adaptiverag.operation": "retrieval.execute",
        "correlation_id": "j-1", "adaptiverag.job_id": "j-1", "adaptiverag.session_id": "s-1",
        "retrieval.mode": "single", "retrieval.k": req.k, "retrieval.evidence_count": len(resp.evidence),
        "retrieval.temporal.kind": resp.resolution.kind}
    assert named(spans, "retrieval.round") == []  # the frozen retrieval has no rounds


def test_every_round_of_the_loop_is_a_child_span_with_the_rounds_own_numbers(spans):
    resp = RetrievalService(make_stack(WITH_MILEAGE), STREAM_ALIASES).handle(
        RetrievalRequest(query=QB, mode="iterative", max_rounds=3, job_id="j-9"))
    parent, rounds, trace = one(spans, "retrieval.execute"), named(spans, "retrieval.round"), resp.trace
    assert len(rounds) == trace.iterations == 2 and parent.attributes["retrieval.max_rounds"] == 3
    for s, r in zip(rounds, trace.rounds):
        assert s.parent.span_id == parent.context.span_id and s.attributes["correlation_id"] == "j-9"
        assert {k: v for k, v in s.attributes.items() if k.startswith("retrieval.")} == {
            "retrieval.round.number": r.iteration, "retrieval.round.strategy": r.strategy,
            "retrieval.round.retrieved_count": len(r.retrieved), "retrieval.round.new_count": len(r.new),
            "retrieval.round.excluded_count": len(r.excluded_versions),
            "retrieval.round.retained_count": len(r.retained), "retrieval.round.context_count": len(r.context),
            "retrieval.round.promoted_count": len(r.promoted),
            "retrieval.coverage.decision": r.coverage.decision,
            "retrieval.coverage.achieved": r.coverage.decision == "sufficient",
            "retrieval.round.decision": r.decision.split(":")[0],
            **({"retrieval.stop_reason": trace.stop_reason} if r is trace.final else {})}
    assert [s.attributes["retrieval.coverage.achieved"] for s in rounds] == [False, True]
    assert [s.attributes["retrieval.round.decision"] for s in rounds] == ["refine", "stop"]
    assert rounds[0].end_time <= rounds[1].start_time  # one after the other


@pytest.mark.parametrize("query, max_rounds", [(QA, 3), (QB, 1), (QB, 2), (QB, 3), ("What is the OC parking fee?", 3)])
def test_the_traced_loop_returns_exactly_what_the_phase_6_loop_returns(query, max_rounds):
    want = IterativeRetriever(max_rounds, STREAM_ALIASES)(make_stack(WITH_MILEAGE), query)
    untraced = TracedIterativeRetriever(max_rounds, STREAM_ALIASES)(make_stack(WITH_MILEAGE), query)
    with capture() as spans:
        traced = TracedIterativeRetriever(max_rounds, STREAM_ALIASES)(make_stack(WITH_MILEAGE), query)
    for got in (untraced, traced):
        assert got.evidence == want.evidence and got.trace == want.trace and got.resolution == want.resolution
    assert len(named(spans, "retrieval.round")) == want.trace.iterations
    assert named(spans, "retrieval.round")[-1].attributes["retrieval.stop_reason"] == want.trace.stop_reason


# ---------------------------------------------------------------- generation


def test_generation_span(spans):
    stack, provider = session_setup()
    retrieval = RetrievalService(stack, SESSION_ALIASES).handle(RetrievalRequest(query=Q_FEB))
    spans.clear()
    req = GenerationRequest(question=Q_FEB, evidence=retrieval.evidence, job_id="j-2", session_id="s-2")
    answer = GenerationService(provider).handle(req).answer
    s = one(spans, "generation.execute")
    assert dict(s.attributes) == {
        "adaptiverag.service": "adaptiverag-generation", "adaptiverag.operation": "generation.execute",
        "correlation_id": "j-2", "adaptiverag.job_id": "j-2", "adaptiverag.session_id": "s-2",
        "generation.provider": provider.name, "generation.model": provider.model,
        "generation.evidence_count": len(req.evidence), "generation.status": "answered",
        "generation.citation_count": len(answer.citations), "generation.claim_count": len(answer.claims),
        "generation.verification": "accepted", "generation.verification.problem_count": 0,
        "generation.llm_calls": 1}  # the stub reports no token usage: no token attribute is invented
    spans.clear()
    abstained = GenerationService(provider).handle(GenerationRequest(question="What is the parking fee?", evidence=[]))
    s = one(spans, "generation.execute")
    assert (s.attributes["generation.status"], s.attributes["generation.abstention_reason"]) == (
        "abstained", abstained.answer.abstention_reason)
    assert (s.attributes["generation.verification"], s.attributes["generation.llm_calls"]) == ("not_run", 0)
    assert s.status.status_code is StatusCode.UNSET  # an abstention is an answer, not an error


def test_generation_span_records_token_usage_and_the_verification_outcome(spans):
    from generation.providers import ProviderError, ProviderResponse

    class Metered:
        name, model = "fake", "fake-1"

        def __init__(self, output, usage):
            self.output, self.usage = output, usage

        def complete(self, system, user, schema):
            if isinstance(self.output, Exception):
                raise self.output
            return ProviderResponse(text=json.dumps(self.output), provider=self.name, model=self.model, usage=self.usage)

    stack, provider = session_setup()
    retrieval = RetrievalService(stack, SESSION_ALIASES).handle(RetrievalRequest(query=Q_FEB))
    usage = {"prompt_tokens": 1200, "completion_tokens": 340, "total_tokens": 1540, "total_time": 0.4}
    spans.clear()
    req = GenerationRequest(question=Q_FEB, evidence=retrieval.evidence, job_id="j-3")
    text = retrieval.evidence[0].chunk.text
    cases = [
        ({"status": "answered", "claims": [{"text": text, "sources": ["S1"], "quotes": [text]}], "not_in_sources": [],
          "abstention_reason": ""}, "answered", "accepted", 0),
        ({"status": "answered", "claims": [{"text": "Charges are due in 45 days", "sources": ["S1"], "quotes": [text]}],
          "not_in_sources": [], "abstention_reason": ""}, "abstained", "rejected", 1),
        ({"status": "insufficient_evidence", "claims": [], "not_in_sources": [], "abstention_reason": "x"}, "abstained",
         "accepted", 0),
    ]
    for output, status, verification, problems in cases:
        GenerationService(Metered(output, usage)).handle(req)
        s = one(spans, "generation.execute")
        assert (s.attributes["generation.status"], s.attributes["generation.verification"],
                s.attributes["generation.verification.problem_count"]) == (status, verification, problems)
        assert (s.attributes["generation.llm_calls"], s.attributes["generation.tokens.prompt"],
                s.attributes["generation.tokens.completion"], s.attributes["generation.tokens.total"]) == (1, 1200, 340, 1540)
        spans.clear()
    GenerationService(Metered(ProviderError("429", 429), usage)).handle(req)  # a provider failure: nothing to verify
    s = one(spans, "generation.execute")
    assert (s.attributes["generation.abstention_reason"], s.attributes["generation.verification"]) == ("provider_error", "not_run")
    assert s.attributes["generation.llm_calls"] == 1 and "generation.tokens.total" not in s.attributes


# ---------------------------------------------------------------- MCP tools and the database


def test_every_mcp_tool_call_is_a_span(spans):
    server = mcp_server()
    ids = {"request_id": "r-1", "job_id": "j-1", "session_id": "s-1"}
    assert not call(server, "document_search", {"query": Q_CARD, **ids}).is_error
    assert not call(server, "metadata_lookup", {"doc_id": "ru"}).is_error
    search, lookup = named(spans, "mcp.tool.execute")
    assert dict(search.attributes) == {
        "adaptiverag.service": "adaptiverag-mcp", "adaptiverag.operation": "mcp.tool.execute",
        "mcp.tool.name": "document_search", "correlation_id": "j-1", "adaptiverag.request_id": "r-1",
        "adaptiverag.job_id": "j-1", "adaptiverag.session_id": "s-1"}
    assert search.parent is None and search.status.status_code is StatusCode.UNSET
    retrieval = one(spans, "retrieval.execute")  # the tool's retrieval, in its worker thread: a child
    assert retrieval.parent.span_id == search.context.span_id and retrieval.attributes["correlation_id"] == "j-1"
    assert lookup.attributes["mcp.tool.name"] == "metadata_lookup"
    assert lookup.attributes["correlation_id"].startswith("mcp-")  # the request id the server made
    assert lookup.context.trace_id != search.context.trace_id  # one trace per call


def test_a_remote_mcp_search_joins_the_retrieval_service_to_the_tools_trace(spans):
    assert not call(mcp_server("remote"), "document_search", {"query": Q_CARD, "mode": "iterative"}).is_error
    tool, retrieval = one(spans, "mcp.tool.execute"), one(spans, "retrieval.execute")
    http, root = ancestors(spans, retrieval)
    assert (http.name, root) == ("POST /retrieve", tool)
    rounds = named(spans, "retrieval.round")
    assert rounds and all(r.parent.span_id == retrieval.context.span_id for r in rounds)
    assert {s.attributes["correlation_id"] for s in spans.get_finished_spans()} == {tool.attributes["correlation_id"]}


def test_mcp_tool_errors_are_error_spans_with_the_tool_error_code(spans):
    class Broken:
        name = "broken"

        def search(self, req):
            raise RuntimeError("SECRET internal detail")

    server = mcp_server()
    catalog = MetadataCatalog(keyword_stack(*MCP_CHUNKS).chunks, MANIFEST, CHUNK_MANIFEST, MCP_ALIASES)
    results = [call(server, "document_search", {"query": Q_CARD, "organization": "Nowhere College"}),
               call(server, "document_search", {"query": "", "limit": 999}),
               call(server, "database_query", {"query_name": "documents"}),
               call(build_server(Tools(catalog, Broken())), "document_search", {"query": Q_CARD})]
    codes = [r.structured_content["error"]["code"] for r in results]
    assert codes == ["unknown_organization", "invalid_arguments", "database_not_configured", "internal_error"]
    got = named(spans, "mcp.tool.execute")
    assert [s.attributes["mcp.error.code"] for s in got] == codes and all(is_error(s) for s in got)
    assert [s.attributes["error.type"] for s in got] == ["ToolFailure", "ValidationError", "ToolFailure",
                                                         "RuntimeError"]
    assert "correlation_id" not in got[1].attributes  # rejected before any id was made
    assert "SECRET" not in dump(spans) and "Nowhere College" not in dump(spans)
    with pytest.raises(MCPError, match="Unknown tool"):
        call(server, "delete_everything", {})
    assert len(named(spans, "mcp.tool.execute")) == 4  # an unknown name is not a tool call: no span


def test_a_named_database_query_is_a_span_under_its_tool_call(spans):
    psycopg = pytest.importorskip("psycopg")
    from tests.test_persistence import URL, FakeConnection, adapter

    _, database = adapter(FakeConnection(rows=[("proc-feb", "s"), ("proc-jul", "s"), ("proc-x", "s")]))
    out = call(mcp_server(database=database), "database_query", {
        "query_name": "document_versions", "parameters": {"series_id": "SECRET-SERIES"}, "limit": 2, "job_id": "j-db"})
    assert not out.is_error and out.structured_content["row_count"] == 2
    tool, db = one(spans, "mcp.tool.execute"), one(spans, "db.query")
    assert db.parent.span_id == tool.context.span_id and db.status.status_code is StatusCode.UNSET
    assert dict(db.attributes) == {
        "adaptiverag.service": "adaptiverag-persistence", "adaptiverag.operation": "db.query",
        "correlation_id": "j-db", "adaptiverag.job_id": "j-db",
        "adaptiverag.request_id": out.structured_content["correlation"]["request_id"],
        "db.system.name": "postgresql", "db.operation.name": "SELECT", "db.query.name": "document_versions",
        "db.namespace": "adaptiverag", "db.response.returned_rows": 2, "db.query.truncated": True}
    spans.clear()

    for database, kind in [(adapter(fail=psycopg.OperationalError("connection refused"))[1], "DatabaseError"),
                           (adapter(FakeConnection(loaded=False))[1], "CorpusNotLoaded"),
                           (adapter(FakeConnection(fail=psycopg.errors.QueryCanceled("timeout")))[1],
                            "StatementFailed")]:
        failed = call(mcp_server(database=database), "database_query", {"query_name": "documents"})
        assert failed.structured_content["error"]["code"] == "database_unavailable"  # the phase 9 mapping, unchanged
        tool, db = one(spans, "mcp.tool.execute"), one(spans, "db.query")
        assert is_error(db, kind) and db.attributes["db.query.name"] == "documents"
        assert is_error(tool, "ToolFailure") and tool.attributes["mcp.error.code"] == "database_unavailable"
        text = dump(spans)
        for secret in (URL, "secret", "db.invalid", "SELECT doc_id", "SECRET-SERIES", "proc-feb"):
            assert secret not in text, secret
        spans.clear()


# ---------------------------------------------------------------- failures


def test_a_failing_retrieval_marks_every_span_on_its_path_and_keeps_the_error_mapping(spans, monkeypatch):
    def boom(stack, query, k):
        raise RuntimeError(f"index unavailable while searching for {query}")

    monkeypatch.setattr("services.retrieval.service.retrieve", boom)
    stack, provider = session_setup()
    with pytest.raises(RuntimeError, match="index unavailable") as info:  # the original exception, at the boundary
        RetrievalService(stack, SESSION_ALIASES).handle(RetrievalRequest(query=Q_FEB))
    assert type(info.value) is RuntimeError and is_error(one(spans, "retrieval.execute"), "RuntimeError")
    spans.clear()

    rig = Rig(stack, provider, SESSION_ALIASES)
    r = rig.query("s1", Q_FEB)
    assert r.status_code == 502 and r.json()["error"]["type"] == "RuntimeError"  # phase 7's answer, unchanged
    assert rig.store.get("s1") is None and provider.calls == []
    retrieval, http = one(spans, "retrieval.execute"), one(spans, "POST /retrieve")
    assert is_error(retrieval, "RuntimeError") and [e.name for e in retrieval.events] == ["exception"]
    assert is_error(http, "RuntimeError") and http.attributes["http.response.status_code"] == 500
    orchestrator = one(spans, "orchestrator.execute")
    assert is_error(orchestrator, "RuntimeError") and orchestrator.attributes["job.status"] == "failed"
    assert is_error(one(spans, "POST /query"), "502")
    assert named(spans, "generation.execute") == [] and Q_FEB not in dump(spans)


def test_a_failing_round_ends_its_span_as_an_error(spans, monkeypatch):
    calls = []

    def second_fails(stack, query, k):
        calls.append(query)
        if len(calls) == 2:
            raise RuntimeError("reranker crashed")
        return real(stack, query, k)

    import adaptive.streaming.controller as loop

    real = loop.retrieve
    monkeypatch.setattr(loop, "retrieve", second_fails)
    with pytest.raises(RuntimeError, match="reranker crashed"):
        RetrievalService(make_stack(WITH_MILEAGE), STREAM_ALIASES).handle(RetrievalRequest(query=QB, mode="iterative"))
    first, second = named(spans, "retrieval.round")
    assert first.attributes["retrieval.round.decision"] == "refine" and not is_error(first)
    assert is_error(second, "RuntimeError") and is_error(one(spans, "retrieval.execute"), "RuntimeError")


# ---------------------------------------------------------------- tracing can fail; the application cannot


class RaisingTracer:
    def start_span(self, *a, **kw):
        raise RuntimeError("tracer is broken")


class RaisingSpan:
    def __getattr__(self, name):
        raise RuntimeError(f"span.{name} is broken")


class RaisingSpanTracer:
    def start_span(self, *a, **kw):
        return RaisingSpan()


class RaisingPropagator:
    def inject(self, *a, **kw):
        raise RuntimeError("inject is broken")

    def extract(self, *a, **kw):
        raise RuntimeError("extract is broken")


def conversation(rig):
    out = []
    for question in (Q_FEB, "Does that apply currently?", Q_TWO):
        r = rig.query("conv", question, request_id="req")
        out.append((r.status_code, {k: v for k, v in r.json().items() if k != "job_id"}))
    return out


@pytest.mark.parametrize("mode", ["single", "iterative"])
@pytest.mark.parametrize("breakage", ["none", "tracer", "span", "propagator"])
def test_tracing_never_changes_or_breaks_a_query(mode, breakage, monkeypatch):
    """The same conversation with tracing off, on, and broken in three ways: identical responses, model input and
    stored session."""
    stack, provider = session_setup()
    baseline_rig = Rig(stack, provider, SESSION_ALIASES, mode)
    baseline = conversation(baseline_rig)  # tracing off
    stack2, provider2 = session_setup()
    rig = Rig(stack2, provider2, SESSION_ALIASES, mode)
    with capture() as spans:
        if breakage == "tracer":
            monkeypatch.setattr(telemetry_provider, "tracer", lambda: RaisingTracer())
        elif breakage == "span":
            monkeypatch.setattr(telemetry_provider, "tracer", lambda: RaisingSpanTracer())
        elif breakage == "propagator":
            monkeypatch.setattr("services.telemetry.asgi._PROPAGATOR", RaisingPropagator())
        assert conversation(rig) == baseline
        traced = len(spans.get_finished_spans())
    assert provider2.calls == provider.calls  # byte-identical model input
    assert rig.store.get("conv") == baseline_rig.store.get("conv")
    assert [r.model_dump(exclude={"job_id"}) for r in rig.retrieval.requests] == [
        r.model_dump(exclude={"job_id"}) for r in baseline_rig.retrieval.requests]
    assert (traced > 0) == (breakage in ("none", "propagator"))


@pytest.mark.parametrize("tracer", [RaisingTracer, RaisingSpanTracer])
def test_broken_tracing_does_not_break_mcp_tools_or_change_their_errors(tracer, monkeypatch):
    server = mcp_server()
    want = call(server, "document_search", {"query": Q_CARD, "request_id": "r"}).structured_content
    want_error = call(server, "database_query", {"query_name": "documents", "request_id": "r"}).structured_content
    monkeypatch.setattr(telemetry_provider, "tracer", lambda: tracer())
    assert call(server, "document_search", {"query": Q_CARD, "request_id": "r"}).structured_content == want
    assert call(server, "database_query", {"query_name": "documents", "request_id": "r"}).structured_content == want_error
    with pytest.raises(KeyError):  # an application exception still comes out as itself
        with telemetry.span("generation.execute", telemetry.GENERATION):
            raise KeyError("x")


# ---------------------------------------------------------------- no payload in any span


def test_no_payload_reaches_a_span(spans):
    """Questions, answers, chunk text, document titles, headers and connection strings are in no exported span."""
    stack, provider = session_setup()
    rig = Rig(stack, provider, SESSION_ALIASES, "iterative")
    secrets = {"Bearer SECRET-TOKEN", "SECRET-TOKEN", "sid=SECRET-COOKIE", "SECRET-COOKIE", "Authorization", "authorization",
               "Cookie", "cookie"}
    for question in (Q_FEB, "Does that apply currently?", Q_TWO):
        r = rig.api.post("/query", json={"session_id": "conv", "question": question},
                         headers={"Authorization": "Bearer SECRET-TOKEN", "Cookie": "sid=SECRET-COOKIE"})
        assert r.status_code == 200
        secrets |= {question, r.json()["text"], r.json()["resolution"]["query"]}
    secrets |= {x.query for x in rig.retrieval.requests} | {x.question for x in rig.generation.requests}
    assert not call(mcp_server(), "document_search", {"query": Q_CARD, "organization": "TU"}).is_error
    assert not call(mcp_server(), "metadata_lookup", {"chunk_id": MCP_CHUNKS[0].chunk_id, "include_text": True}).is_error
    for chunk in [*SESSION_CHUNKS, *MCP_CHUNKS]:
        secrets |= {chunk.text, chunk.chunk_id, chunk.title, chunk.organization}
    secrets |= {Q_CARD, *(d["filename"] for d in MANIFEST["documents"])}
    secrets = {s for s in secrets if s}

    finished = spans.get_finished_spans()
    assert {s.name for s in finished} >= {"POST /query", "orchestrator.execute", "POST /retrieve", "retrieval.execute",
                                          "retrieval.round", "POST /generate", "generation.execute", "mcp.tool.execute"}
    text = dump(spans)
    for secret in secrets:
        assert secret not in text, secret
    for s in finished:
        assert set(s.attributes) <= telemetry.ATTRIBUTES, s.name
        assert all(isinstance(v, (str, bool, int, float)) for v in s.attributes.values()), s.name
        assert all(set(e.attributes) <= {"exception.type"} for e in s.events), s.name
        assert all(len(v) <= 64 for v in s.attributes.values() if isinstance(v, str)), s.name  # ids and codes only


# ---------------------------------------------------------------- isolation


FROZEN = ["retrieval", "generation", "temporal", "chunking", "ingestion", "adaptive", "config"]


def test_layering_opentelemetry_only_in_services_telemetry_and_not_in_the_frozen_core():
    for path in ROOT.glob("**/*.py"):
        rel = path.relative_to(ROOT)
        if rel.parts[0] in (".venv", "tests"):
            continue
        source = path.read_text(encoding="utf-8")
        if rel.parts[:2] != ("services", "telemetry"):
            assert "import opentelemetry" not in source and "from opentelemetry" not in source, rel
        if rel.parts[0] in FROZEN:
            assert "telemetry" not in source, rel
