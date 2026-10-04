"""Service architecture (phase 7, offline): contracts, the api, the orchestrator, the retrieval and generation
services, correlation ids, the phase 4 queue and the phase 5 store, and above all that a query routed through the
services gives exactly what the direct pipeline gives.

Every service runs in-process (FastAPI TestClient) over the in-memory broker, with the keyword stand-ins and stub
models of the phase 3 and phase 6 tests. The orchestrator gets only the chunk catalog (no index): every retrieval must
go through the retrieval service. No network, no models.
"""

from dataclasses import asdict

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from adaptive.jobs.broker import InMemoryBroker
from adaptive.jobs.contracts import EVENTS_QUEUE, JOBS_QUEUE, JobEvent, JobRequest, JobStatus
from adaptive.session.controller import SessionController
from adaptive.session.state import Session
from adaptive.session_store.codec import decode_session, encode_session
from adaptive.session_store.redis_store import RedisSessionStore
from adaptive.session_store.store import InMemorySessionStore
from adaptive.streaming.controller import IterativeRetriever
from generation.answer import GroundedAnswerer
from generation.pipeline import retrieve
from generation.providers import StubProvider
from services import correlation
from services.api.app import create_app as api_app
from services.contracts import (GenerationRequest, QueryRequest, QueryResponse, RetrievalRequest, RetrievalResponse)
from services.generation.app import create_app as generation_app
from services.generation.client import GenerationClient, NoLocalModel
from services.generation.service import GenerationService
from services.http import ServiceError
from services.orchestrator.client import OrchestratorClient, QueryTimeout
from services.orchestrator.worker import OrchestratorWorker, build_controller, catalog_stack
from services.retrieval.app import create_app as retrieval_app
from services.retrieval.client import RetrievalClient
from services.retrieval.service import RetrievalService
from tests.test_session import ALIASES as SESSION_ALIASES, CHUNKS as SESSION_CHUNKS, Q_FEB, Q_TWO, top_source
from tests.test_multi_intent_controller import stack as keyword_stack
from tests.test_session_store import FakeRedis
from tests.test_streaming import ALIASES as STREAM_ALIASES, QB, WITH_MILEAGE, make_stack, provider_for

FOLLOW = "Does that apply currently?"
CONVERSATION = [Q_FEB, FOLLOW, Q_TWO, FOLLOW]  # self-contained, temporal follow-up, two intents, unresolved


class Recording(RetrievalService):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.requests = []

    def handle(self, req):
        self.requests.append(req)
        return super().handle(req)


class RecordingGeneration(GenerationService):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.requests = []

    def handle(self, req):
        self.requests.append(req)
        return super().handle(req)


class Rig:
    """api -> orchestrator (phase 4 worker, in-process) -> retrieval / generation services, all in one process."""

    def __init__(self, stack, provider, aliases, mode="single", store=None, broken_retrieval=False):
        self.retrieval = Recording(stack, aliases)
        self.generation = RecordingGeneration(provider)
        transport = httpx.Client(base_url="http://127.0.0.1:9") if broken_retrieval else TestClient(
            retrieval_app(self.retrieval))
        self.broker = InMemoryBroker()
        self.store = InMemorySessionStore() if store is None else store
        controller = build_controller(catalog_stack(stack.chunks), RetrievalClient(transport, mode),
                                      GenerationClient(TestClient(generation_app(self.generation))), aliases)
        self.worker = OrchestratorWorker(controller, self.broker, sessions=self.store)
        self.orchestrator = OrchestratorClient(lambda: self.broker, self.store, 5, pump=self.worker.run)
        self.api = TestClient(api_app(self.orchestrator))

    def query(self, session_id, question, **extra):
        return self.api.post("/query", json={"session_id": session_id, "question": question, **extra})


def session_setup():
    return keyword_stack(*SESSION_CHUNKS), StubProvider(top_source)


def view(turn) -> dict:
    """What the direct pipeline would put in a QueryResponse for this turn."""
    q = QueryResponse.of_turn("r", "j", "s", turn).model_dump()
    return {k: v for k, v in q.items() if k not in ("request_id", "job_id", "session_id")}


# ---------------------------------------------------------------- A, B, J: health and contracts


def test_health_endpoints():
    stack, provider = session_setup()
    rig = Rig(stack, provider, SESSION_ALIASES)
    assert rig.api.get("/health").json() == {"status": "ok", "service": "api"}
    assert TestClient(retrieval_app(rig.retrieval)).get("/health").json() == {
        "status": "ok", "service": "retrieval", "chunks": len(SESSION_CHUNKS), "reranker": True}
    assert TestClient(generation_app(rig.generation)).get("/health").json()["service"] == "generation"


def test_query_contract():
    stack, provider = session_setup()
    rig = Rig(stack, provider, SESSION_ALIASES)
    r = rig.query("s1", Q_FEB, request_id="client-1")
    assert r.status_code == 200
    body = QueryResponse.model_validate(r.json())
    assert (body.request_id, body.session_id, body.status, body.turn_index) == ("client-1", "s1", "completed", 0)
    assert body.answer_status == "answered" and body.citations and body.citations[0]["chunk_id"]
    assert body.resolution.kind == "self_contained" and body.strategy == "delegate" and body.evidence
    assert len(body.job_id) == 32 and rig.query("s1", Q_FEB).json()["request_id"] != "client-1"  # else generated


@pytest.mark.parametrize("payload", [
    {"session_id": "s1"},                                            # no question
    {"session_id": "s1", "question": ""},                            # empty question
    {"session_id": "bad id!", "question": "q"},                      # session_id characters
    {"session_id": "s1", "question": "q", "extra": 1},               # unknown field
    {"session_id": "s1", "question": "x" * 2001},                    # too long
])
def test_invalid_queries_are_rejected(payload):
    stack, provider = session_setup()
    rig = Rig(stack, provider, SESSION_ALIASES)
    assert rig.api.post("/query", json=payload).status_code == 422
    assert rig.broker.published == []  # nothing reached the queue


def test_invalid_service_contracts_are_rejected():
    for bad in [dict(query="q", mode="agentic"), dict(query="q", max_rounds=0), dict(query=""), dict(query="q", x=1)]:
        with pytest.raises(ValidationError):
            RetrievalRequest(**bad)
    with pytest.raises(ValidationError):
        GenerationRequest(question="q", evidence=[], extra=1)
    with pytest.raises(ValidationError):
        QueryResponse.decode(b'{"request_id":"r","job_id":"j","session_id":"s","status":"done"}')
    stack, provider = session_setup()
    rig = Rig(stack, provider, SESSION_ALIASES)
    assert TestClient(retrieval_app(rig.retrieval)).post("/retrieve", json={"query": "q", "mode": "x"}).status_code == 422
    assert TestClient(generation_app(rig.generation)).post("/generate", json={"question": "q"}).status_code == 422


# ---------------------------------------------------------------- C: the api only delegates


def test_the_api_delegates_to_the_orchestrator():
    class Fake:
        def __init__(self, out=None, exc=None):
            self.out, self.exc, self.seen = out, exc, []

        def query(self, req):
            self.seen.append(req)
            if self.exc:
                raise self.exc
            return self.out

    done = QueryResponse(request_id="r", job_id="j", session_id="s1", status="completed", text="t")
    fake = Fake(done)
    r = TestClient(api_app(fake)).post("/query", json={"session_id": "s1", "question": "What?"})
    assert r.status_code == 200 and r.json() == done.model_dump(mode="json")
    assert fake.seen == [QueryRequest(session_id="s1", question="What?")]
    failed = QueryResponse(request_id="r", job_id="j", session_id="s1", status="failed",
                           error={"type": "ServiceError", "message": "m"})
    assert TestClient(api_app(Fake(failed))).post("/query", json={"session_id": "s1", "question": "q"}).status_code == 502
    assert TestClient(api_app(Fake(exc=QueryTimeout("t")))).post(
        "/query", json={"session_id": "s1", "question": "q"}).status_code == 504


# ---------------------------------------------------------------- D, K: same behaviour as the direct pipeline


@pytest.mark.parametrize("mode", ["single", "iterative"])
def test_a_conversation_through_the_services_equals_the_direct_pipeline(mode):
    stack, provider = session_setup()
    direct_provider = StubProvider(top_source)
    retriever = IterativeRetriever(aliases=SESSION_ALIASES) if mode == "iterative" else None
    direct = SessionController(stack, direct_provider, SESSION_ALIASES, retriever=retriever)
    session = Session()
    rig = Rig(stack, provider, SESSION_ALIASES, mode)
    for question in CONVERSATION:
        turn = direct.ask(session, question)
        r = rig.query("conv", question)
        assert r.status_code == 200
        got = QueryResponse.model_validate(r.json()).model_dump()
        assert {k: v for k, v in got.items() if k not in ("request_id", "job_id", "session_id")} == view(turn)
    kinds = [t.resolution.kind for t in session.turns]
    assert kinds == ["self_contained", "follow_up", "self_contained", "unresolved"]
    assert session.turns[1].answer.intents[0]["selected_versions"] and session.turns[2].answer.decomposition["multi"]
    assert provider.calls == direct_provider.calls  # byte-identical model input, call for call
    assert asdict(rig.store.get("conv")) == asdict(session)  # the stored session equals the direct one


def test_phase6_loop_runs_in_the_retrieval_service_with_the_same_result():
    stack, provider = make_stack(WITH_MILEAGE), provider_for(WITH_MILEAGE)
    direct_provider = provider_for(WITH_MILEAGE)
    direct = SessionController(stack, direct_provider, STREAM_ALIASES,
                               retriever=IterativeRetriever(aliases=STREAM_ALIASES))
    turn = direct.ask(Session(), QB)
    rig = Rig(stack, provider, STREAM_ALIASES, mode="iterative")
    got = QueryResponse.model_validate(rig.query("s", QB).json())
    assert {k: v for k, v in got.model_dump().items() if k not in ("request_id", "job_id", "session_id")} == view(turn)
    assert "oc::mile" in got.evidence  # the gap the loop closed
    first = rig.retrieval.requests[0]
    assert first.mode == "iterative" and provider.calls == direct_provider.calls


def test_the_orchestrator_never_retrieves_or_calls_a_model_itself():
    with pytest.raises(RuntimeError, match="generation service"):
        NoLocalModel().complete("s", "u", {})
    stack, provider = session_setup()
    rig = Rig(stack, provider, SESSION_ALIASES)
    rig.query("s1", Q_TWO)
    assert rig.worker.controller.multi.stack.hybrid is None  # the catalog has no index
    assert len(rig.retrieval.requests) == 3 and len(rig.generation.requests) >= 1  # whole question + 2 intents


# ---------------------------------------------------------------- E, F: the services preserve the frozen core


@pytest.mark.parametrize("query", [Q_FEB, "What did TU's procedures say currently about card charges?", Q_TWO])
def test_retrieval_service_returns_the_frozen_retrieval(query):
    stack, _ = session_setup()
    client = TestClient(retrieval_app(RetrievalService(stack, SESSION_ALIASES)))
    got = RetrievalResponse.decode(client.post("/retrieve", json={"query": query}).content).retrieval()
    want = retrieve(stack, query)
    assert got.evidence == want.evidence and got.query == want.query
    for f in ("selected", "dropped", "flags"):
        assert getattr(got.resolution, f) == getattr(want.resolution, f)
    assert (got.resolution.intent.kind, got.resolution.intent.dates) == (want.resolution.intent.kind,
                                                                         want.resolution.intent.dates)


def test_retrieval_service_runs_the_phase6_loop():
    stack = make_stack(WITH_MILEAGE)
    client = TestClient(retrieval_app(RetrievalService(stack, STREAM_ALIASES)))
    body = client.post("/retrieve", json={"query": QB, "mode": "iterative", "max_rounds": 3}).content
    got = RetrievalResponse.decode(body).retrieval()
    want = IterativeRetriever(aliases=STREAM_ALIASES)(stack, QB)
    assert got.evidence == want.evidence and got.trace == want.trace and got.trace.iterations == 2


def test_generation_service_returns_the_frozen_answer_and_abstention():
    stack, _ = session_setup()
    for question in (Q_FEB, "What is the parking fee?"):
        r = retrieve(stack, question)
        local_provider, remote_provider = StubProvider(top_source), StubProvider(top_source)
        want = GroundedAnswerer(local_provider).answer(question, r.evidence, question_versions=r.question_versions)
        client = GenerationClient(TestClient(generation_app(GenerationService(remote_provider))))
        got = client.answer(question, r.evidence, question_versions=r.question_versions)
        assert asdict(got) == asdict(want) and remote_provider.calls == local_provider.calls
    assert want.status == "abstained" and want.abstention_reason == "low_relevance"  # the gate, preserved


# ---------------------------------------------------------------- G: the phase 4 queue


def test_queries_travel_as_phase4_jobs_with_the_phase4_lifecycle():
    stack, provider = session_setup()
    rig = Rig(stack, provider, SESSION_ALIASES)
    r = rig.query("s1", Q_FEB).json()
    jobs = [JobRequest.decode(b) for q, b in rig.broker.published if q == JOBS_QUEUE]
    events = [JobEvent.decode(b) for q, b in rig.broker.published if q == EVENTS_QUEUE]
    assert [(j.job_id, j.session_id, j.question) for j in jobs] == [(r["job_id"], "s1", Q_FEB)]
    assert [e.status for e in events] == [JobStatus.PROCESSING, JobStatus.COMPLETED]
    assert events[-1].result.turn_index == r["turn_index"] and events[-1].result.text == r["text"]


def test_a_downstream_failure_fails_the_job_and_the_query():
    stack, provider = session_setup()
    rig = Rig(stack, provider, SESSION_ALIASES, broken_retrieval=True)
    r = rig.query("s1", Q_FEB)
    assert r.status_code == 502 and r.json()["status"] == "failed"
    assert r.json()["error"]["type"] == "ServiceError" and "/retrieve" in r.json()["error"]["message"]
    assert rig.store.get("s1") is None and provider.calls == []  # nothing stored, no model call


# ---------------------------------------------------------------- H: the phase 5 store


def test_sessions_persist_in_the_store_across_orchestrator_restarts():
    backend = FakeRedis()
    stack, provider = session_setup()
    first = Rig(stack, provider, SESSION_ALIASES, store=RedisSessionStore(client=backend))
    assert first.query("s1", Q_FEB).status_code == 200
    # a new orchestrator and api, new store instances; only the stored bytes remain
    second = Rig(stack, StubProvider(top_source), SESSION_ALIASES, store=RedisSessionStore(client=backend))
    r = second.query("s1", FOLLOW).json()
    assert (r["resolution"]["kind"], r["turn_index"], r["resolution"]["anchor"]) == ("follow_up", 1, 0)
    stored = RedisSessionStore(client=backend).get("s1")
    assert len(stored.turns) == 2 and decode_session(encode_session(stored)) == stored
    assert list(backend.data) == ["adaptiverag:session:s1"]


# ---------------------------------------------------------------- I: correlation ids


def test_correlation_ids_survive_every_boundary():
    stack, provider = session_setup()
    rig = Rig(stack, provider, SESSION_ALIASES)
    r = rig.query("conv-7", Q_TWO, request_id="req-42").json()
    assert (r["request_id"], r["session_id"]) == ("req-42", "conv-7")
    seen = rig.retrieval.requests + rig.generation.requests
    assert seen and {(x.job_id, x.session_id) for x in seen} == {(r["job_id"], "conv-7")}
    assert correlation.current() == {"request_id": None, "job_id": None, "session_id": None}  # nothing leaks


def test_a_response_with_other_ids_is_refused():
    class Echo(httpx.BaseTransport):
        def handle_request(self, request):
            body = RetrievalResponse(job_id="someone-else", query="q", mode="single", evidence=[],
                                     resolution={"kind": "neutral"}).encode()
            return httpx.Response(200, content=body)

    client = RetrievalClient(httpx.Client(transport=Echo(), base_url="http://retrieval"))
    with correlation.bind(job_id="mine", session_id="s"):
        with pytest.raises(ServiceError, match="correlation ids"):
            client(None, "q")
