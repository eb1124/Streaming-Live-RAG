"""Bounded iterative retrieval (phase 6, offline): coverage, refinement, accumulation, the iteration budget, one
generation after the loop, and phases 1 to 5 unchanged around it.

The frozen pipeline runs unchanged over the keyword-overlap stand-ins of tests/test_multi_intent_controller.py
(cross-encoder logit = shared words - 2, so two shared words are exactly relevant). The stub model answers from the
top-ranked source [S1]. No network, no models.
"""

import json
from dataclasses import asdict

import pytest

import adaptive.streaming.controller as streaming_controller
from adaptive.controller import AdaptiveController
from adaptive.jobs.broker import InMemoryBroker
from adaptive.jobs.client import JobClient
from adaptive.jobs.contracts import JobStatus
from adaptive.jobs.worker import Worker
from adaptive.multi.controller import MultiIntentController
from adaptive.session.controller import SessionController
from adaptive.session.state import Session
from adaptive.session_store.codec import decode_session, encode_session
from adaptive.session_store.redis_store import RedisSessionStore
from adaptive.streaming.controller import IterativeRetriever, StreamingController
from adaptive.streaming.refine import keywords, same_temporal_intent, strip_organizations
from adaptive.streaming.state import (INITIAL, KEYWORD_FOCUS, ORGANIZATION_FOCUS, STOP_MAX_ROUNDS, STOP_NO_REFINEMENT,
                                     STOP_STRUCTURAL, STOP_SUFFICIENT, SUFFICIENT)
from generation import config as C
from generation.pipeline import RetrievalStack, retrieve
from generation.providers import StubProvider
from retrieval.rerank import RerankedHybridRetriever
from temporal.intent import parse_query
from temporal.resolve import TemporalResolver
from tests.test_generation import answered, mk
from tests.test_generation_verification import FEB_CARD, JUL_CARD, SERIES
from tests.test_multi_intent_controller import KeywordCrossEncoder, KeywordHybrid, label_of
from tests.test_session_store import FakeRedis

ALIASES = {"TU": "Test University", "OC": "Other College"}
GOLD = mk("gold", "TU card deadline: card charges are due 30 days after the trip deadline.", doc="tu-card")
FILLERS = [mk(f"f{i}", f"Card deadline notice n{i}: card charges have a deadline.", doc="tu-card") for i in range(10)]
MILEAGE = mk("mile", "Other College mileage rate is 70 cents per mile.", org="Other College", doc="oc")
LIBRARY = mk("lib", "Library opens at nine.", org="Other College", doc="oc-lib")
WITH_MILEAGE = [GOLD, *FILLERS, MILEAGE, LIBRARY]
WITHOUT_MILEAGE = [GOLD, *FILLERS, LIBRARY]

QA = "What is the TU card deadline?"  # sufficient at once
QB = "What is the TU card deadline and the OC mileage rate?"  # OC missing from round 1
QH = "What is the OC parking fee?"  # nothing relevant anywhere


class LoggingHybrid(KeywordHybrid):
    def __init__(self, chunks, events):
        super().__init__(chunks)
        self.events = events

    def fuse(self, query):
        self.events.append(("retrieve", query))
        return super().fuse(query)


def make_stack(chunks, events=None, rerank=True):
    hybrid = LoggingHybrid(chunks, events if events is not None else [])
    reranker = RerankedHybridRetriever(hybrid, KeywordCrossEncoder()) if rerank else None
    return RetrievalStack(chunks, hybrid, reranker, TemporalResolver.from_chunks(chunks))


def provider_for(chunks, events=None):
    def script(system, user):
        if events is not None:
            events.append(("generate", None))
        for c in chunks:
            if label_of(user, c) == "S1":
                return answered((c.text, ["S1"], [c.text]))
        return {"status": "insufficient_evidence", "claims": [], "not_in_sources": [], "abstention_reason": "none"}

    return StubProvider(script)


def controller(chunks=WITH_MILEAGE, max_rounds=3, events=None):
    return StreamingController(make_stack(chunks, events), provider_for(chunks, events), max_rounds, ALIASES)


def ids(evidence):
    return [e.chunk.chunk_id for e in evidence]


# ---------------------------------------------------------------- A: one round, exactly the frozen path


def test_sufficient_first_round_is_exactly_the_frozen_path():
    ctl = controller()
    result = ctl.run(QA)
    t = result.trace
    assert (t.iterations, t.stop_reason, t.rounds[0].strategy) == (1, STOP_SUFFICIENT, INITIAL)
    assert t.rounds[0].coverage.decision == SUFFICIENT and t.rounds[0].decision == "stop: sufficient"
    frozen_stack = make_stack(WITH_MILEAGE)
    frozen_provider = provider_for(WITH_MILEAGE)
    frozen = AdaptiveController(frozen_stack, frozen_provider).run(QA)
    assert ctl.provider.calls == frozen_provider.calls and len(ctl.provider.calls) == 1  # identical model input
    assert (result.answer.text, result.answer.citations) == (frozen.text, frozen.citations)
    r = ctl.retriever(ctl.stack, QA)
    assert r.evidence == retrieve(frozen_stack, QA).evidence  # the frozen evidence, unchanged


# ---------------------------------------------------------------- B, D, E, G: refine, accumulate, one generation


def test_insufficient_first_round_refines_and_accumulates():
    events = []
    ctl = controller(events=events)
    result = ctl.run(QB)
    t = result.trace
    assert (t.iterations, t.stop_reason) == (2, STOP_SUFFICIENT)
    r1, r2 = t.rounds
    assert r1.coverage.signals["gaps"] == ["Other College"] and r1.decision == "refine: organization_focus (Other College)"
    assert (r2.strategy, r2.target) == (ORGANIZATION_FOCUS, "Other College")
    assert r2.query == "What is the card deadline and the OC mileage rate (Other College)"
    assert MILEAGE.chunk_id not in r1.retained and r2.new == [MILEAGE.chunk_id]
    assert r2.promoted == [MILEAGE.chunk_id] and MILEAGE.chunk_id in r2.context  # the gap is closed in the context
    # G: every retrieval happens before the single generation
    assert [e[0] for e in events] == ["retrieve", "retrieve", "generate"] and len(ctl.provider.calls) == 1
    assert result.answer.status == "answered"


def test_duplicate_evidence_is_kept_once():
    t = controller().run(QB).trace
    r2 = t.rounds[1]
    assert len(set(r2.retrieved) & set(t.rounds[0].retained)) >= 5  # round 2 retrieved much of round 1 again
    assert len(r2.retained) == len(set(r2.retained)) == 10


def test_stronger_evidence_is_not_displaced():
    ctl = controller()
    r = ctl.retriever(ctl.stack, QB)
    first = retrieve(make_stack(WITH_MILEAGE), QB)
    final = ids(r.evidence)
    # the strongest chunk stays first; the five best of round 1 keep their order; the promotion takes the last
    # context slot and the chunk it displaces moves down one place, still retained
    assert final[0] == GOLD.chunk_id and final[:5] == ids(first.evidence)[:5]
    assert final[C.MAX_SOURCES - 1] == MILEAGE.chunk_id and final[C.MAX_SOURCES] == ids(first.evidence)[5]
    scores = {e.chunk.chunk_id: e.rerank_score for e in r.evidence}
    assert scores[GOLD.chunk_id] == 1.0 and scores[MILEAGE.chunk_id] == 0.0  # scores for the question itself
    assert [e.rank for e in r.evidence] == list(range(1, 11))


# ---------------------------------------------------------------- C, F, H: the budget and stopping


@pytest.mark.parametrize("max_rounds", [1, 2, 3])
def test_the_loop_stops_at_the_configured_maximum(max_rounds):
    t = controller(WITHOUT_MILEAGE, max_rounds).run(QB).trace
    assert t.iterations == max_rounds and t.stop_reason == STOP_MAX_ROUNDS
    assert all(r.coverage.decision == "insufficient" for r in t.rounds)


def test_no_query_is_issued_twice_and_the_loop_ends_when_refinements_run_out():
    t = controller(WITHOUT_MILEAGE, max_rounds=10).run(QB).trace
    assert t.stop_reason == STOP_NO_REFINEMENT and t.iterations == 3
    assert [r.strategy for r in t.rounds] == [INITIAL, ORGANIZATION_FOCUS, KEYWORD_FOCUS]
    assert len({q.casefold() for q in t.queries}) == len(t.queries)
    assert {s["why"] for s in t.skipped} == {"already issued"}


def test_insufficient_after_the_budget_keeps_the_frozen_abstention():
    ctl = controller()
    result = ctl.run(QH)
    frozen = AdaptiveController(make_stack(WITH_MILEAGE), provider_for(WITH_MILEAGE)).run(QH)
    assert result.trace.stop_reason == STOP_MAX_ROUNDS and result.trace.iterations == 3
    assert (result.answer.status, result.answer.abstention_reason) == ("abstained", frozen.abstention_reason)
    assert frozen.abstention_reason == "low_relevance" and ctl.provider.calls == []  # the gate, no model call
    assert all(e.rerank_score < C.MIN_RERANK_LOGIT for e in ctl.retriever(ctl.stack, QH).evidence)


def test_structurally_unresolved_requests_stop_at_once():
    t = controller().run("What is the Harvard University card deadline?").trace
    assert (t.iterations, t.stop_reason) == (1, STOP_STRUCTURAL)
    assert t.rounds[0].coverage.signals["outside_corpus"] == ["Harvard University"]


def test_without_scores_coverage_is_not_measured_and_one_round_runs():
    stack = make_stack(WITH_MILEAGE, rerank=False)
    r = IterativeRetriever(aliases=ALIASES)(stack, QB)
    assert r.trace.iterations == 1 and "cannot be measured" in r.trace.rounds[0].coverage.reason
    assert r.evidence == retrieve(stack, QB).evidence


def test_max_rounds_must_be_positive():
    with pytest.raises(ValueError):
        IterativeRetriever(max_rounds=0)


# ---------------------------------------------------------------- refinement keeps the temporal intent


def test_refinements_keep_the_temporal_intent_and_excluded_versions_stay_out():
    chunks = [FEB_CARD, JUL_CARD, *FILLERS, MILEAGE]
    q = "What did TU's February 2026 procedures say about card charges not submitted, and the OC mileage rate?"
    ctl = StreamingController(make_stack(chunks), provider_for(chunks), 3, ALIASES)
    r = ctl.retriever(ctl.stack, q)
    assert r.resolution.selected == {SERIES: ["proc-feb"]} and r.trace.iterations >= 2
    assert all(same_temporal_intent(q, x) for x in r.trace.queries)
    assert all(JUL_CARD.chunk_id not in rd.retained for rd in r.trace.rounds)


def test_a_refinement_that_would_change_the_temporal_intent_is_not_issued(monkeypatch):
    monkeypatch.setattr(streaming_controller, "refinements",
                        lambda q, cov, aliases: [(KEYWORD_FOCUS, None, "card deadline OC mileage rate currently")])
    t = controller(WITHOUT_MILEAGE).run(QB).trace
    assert t.iterations == 1 and t.stop_reason == STOP_NO_REFINEMENT
    assert t.skipped == [{"strategy": KEYWORD_FOCUS, "query": "card deadline OC mileage rate currently",
                          "why": "would change the temporal intent"}]


def test_keyword_and_organization_refinements():
    assert keywords("What is the TU card deadline, and the OC mileage rate?") == "TU card deadline OC mileage rate"
    for q in ("What changed on or after August 1, 2026?", "What did the February 2026 procedures say?",
              "Is it current?"):
        assert same_temporal_intent(q, keywords(q)), q
    assert strip_organizations("At TU, what is the rule, and at OC, what is the fee?", ["Test University"],
                               ALIASES) == "what is the rule, and at OC, what is the fee?"
    assert parse_query(keywords("effective July 1, 2026")).kind == "point_in_time"


# ---------------------------------------------------------------- L: determinism


def test_identical_inputs_give_identical_decisions_and_queries():
    a, b = controller().run(QB), controller().run(QB)
    assert a.trace.to_dict() == b.trace.to_dict() and a.answer.text == b.answer.text
    assert json.dumps(a.trace.to_dict(), sort_keys=True)  # JSON-ready (no timings, no objects)


# ---------------------------------------------------------------- I, J, K: sessions, jobs, the session store


def session_controller(traces, chunks=WITH_MILEAGE):
    stack = make_stack(chunks)
    return SessionController(stack, provider_for(chunks), ALIASES,
                             retriever=IterativeRetriever(aliases=ALIASES, observer=traces.append)), stack


def test_follow_ups_resolve_as_before_and_retrieve_afresh_for_the_rewritten_query():
    traces = []
    ctl, stack = session_controller(traces)
    plain = SessionController(make_stack(WITH_MILEAGE), provider_for(WITH_MILEAGE), ALIASES)
    s, s_plain = Session(), Session()
    for q in (QA, "Is that deadline still the same after the trip?"):
        t, t_plain = ctl.ask(s, q), plain.ask(s_plain, q)
        assert t.resolution == t_plain.resolution  # phase 3 resolution unchanged
        assert traces[-1].question == t.resolution.query  # the loop ran on the (rewritten) query of this turn
    follow = s.turns[1]
    assert follow.resolution.kind == "follow_up" and follow.resolution.query.startswith("(Earlier in this conversation")
    # the turn's first round is a fresh retrieval of its own query: nothing is carried over from turn 1
    assert traces[-1].rounds[0].retrieved == ids(retrieve(stack, follow.resolution.query).evidence)


def test_phase2_runs_the_loop_for_the_question_and_each_intent():
    traces = []
    ctl, _ = session_controller(traces)
    t = ctl.ask(Session(), "What is the TU card deadline, and what is the OC mileage rate?")
    assert t.answer.decomposition["multi"] and len(traces) == 3  # the whole question, then each intent
    assert [x.question for x in traces[1:]] == [i["sub_query"] for i in t.answer.intents]


def test_jobs_keep_their_lifecycle_with_the_loop():
    traces = []
    ctl, _ = session_controller(traces)
    broker = InMemoryBroker()
    worker, client = Worker(ctl, broker), JobClient(broker)
    jobs = [client.submit(q, "s1") for q in (QB, "Does OC have the same rule?")]
    worker.run()
    client.poll()
    Q, P, Cm = JobStatus.QUEUED, JobStatus.PROCESSING, JobStatus.COMPLETED
    assert [client.tracker.history[j] for j in jobs] == [[Q, P, Cm], [Q, P, Cm]]
    assert client.result(jobs[0]).answer_status == "answered"
    assert (client.result(jobs[1]).resolution, client.result(jobs[1]).answer_status) == ("unresolved", "abstained")


def test_the_session_store_persists_the_turns_and_no_loop_state():
    traces = []
    ctl, _ = session_controller(traces)
    store = RedisSessionStore(client=FakeRedis())
    broker = InMemoryBroker()
    worker, client = Worker(ctl, broker, sessions=store), JobClient(broker)
    for q in (QB, "Is that deadline still the same after the trip?"):
        client.submit(q, "s1")
        worker.run()
    stored = store.get("s1")
    assert len(stored.turns) == 2 and decode_session(encode_session(stored)) == stored
    body = encode_session(stored).decode()
    assert '"rounds"' not in body and '"stop_reason"' not in body  # the trace is execution state, not session
    assert asdict(stored)["turns"][0].keys() == {"index", "question", "resolution", "answer", "reused_citations"}


# ---------------------------------------------------------------- M: the frozen default


def test_phases_2_and_3_default_to_the_frozen_retrieval():
    stack = make_stack(WITH_MILEAGE)
    assert MultiIntentController(stack, provider_for(WITH_MILEAGE), ALIASES).retrieve is retrieve
    assert SessionController(stack, provider_for(WITH_MILEAGE), ALIASES).multi.retrieve is retrieve
