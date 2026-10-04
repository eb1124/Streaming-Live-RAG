"""The turn gate (offline): unfinished utterances wait, acknowledgements are suppressed, layout requests re-present the
stored answer, and everything else takes the existing retrieval path unchanged. Asserted through the session
controller, the job worker and the api; no network, no models (the stand-ins of tests/test_session.py).
"""

import pytest

from adaptive.jobs.contracts import JobResult
from adaptive.session.controller import SessionController
from adaptive.session.gate import RETRIEVE, decide, join, present
from adaptive.session.state import (FOLLOW_UP, PRESENTATION, SELF_CONTAINED, SUPPRESS, UNRESOLVED, WAIT, WAITING_TEXT,
                                    Session)
from adaptive.session_store.codec import decode_session, encode_session
from generation.providers import StubProvider
from services.contracts import QueryResponse
from services.telemetry.testing import capture
from tests.test_generation import answered
from tests.test_multi_intent_controller import insufficient_out, label_of, stack
from tests.test_services import Rig
from tests.test_session import ADVANCE, ALIASES, CHUNKS, LODGE, SETTLE

Q_ADV = "What are the Rutgers travel advance requirements?"
Q_ADV_HEAD, Q_ADV_TAIL = "What are the Rutgers travel...", "advance requirements?"
Q_LODGE = "What is TU's rule for a multi-bedroom accommodation?"
BULLETS = "Give me that in two bullet points."
THANKS = "Thanks, that's all."


def every_source(system, user):
    """One claim per source of the conversation's chunks that is in the prompt, in the corpus's order."""
    claims = [(c.text, [label_of(user, c)], [c.text]) for c in (ADVANCE, SETTLE, LODGE) if label_of(user, c)]
    return answered(*claims[:3]) if claims else insufficient_out()


class Counted:
    """A session controller whose retrievals and model calls are counted."""

    def __init__(self, script=every_source):
        self.provider = StubProvider(script)
        self.controller = SessionController(stack(*CHUNKS), self.provider, ALIASES)
        self.retrievals = []
        base = self.controller.multi.retrieve
        self.controller.multi.retrieve = lambda st, q, k=10: self.retrievals.append(q) or base(st, q, k)
        self.session = Session()

    def ask(self, question):
        return self.controller.ask(self.session, question)

    @property
    def work(self):
        return len(self.retrievals), len(self.provider.calls)


# ---------------------------------------------------------------- the decision (pure)


@pytest.mark.parametrize("utterance", [
    "What is the UConn travel...", "What is the UConn travel…", "What are the", "What is", "How",
    "What are UConn's", "the rules for travel and", "What is the limit for lodging,", "Rutgers travel advance -",
])
def test_unfinished_utterances_wait(utterance):
    d = decide(utterance)
    assert d.kind == WAIT and d.signals == {"retrieval_required": False}


@pytest.mark.parametrize("utterance", [
    "Thanks, that's all.", "thanks", "Thank you very much!", "Ok, got it.", "Great, thanks for the help", "ok",
    "That's all for now", "Perfect. No more questions.", "Understood", "Bye", "Thanks…",
])
def test_acknowledgements_and_closings_are_suppressed(utterance):
    d = decide(utterance)
    assert d.kind == SUPPRESS and d.signals == {"retrieval_required": False}


@pytest.mark.parametrize("utterance, layout", [
    ("Give me that in two bullet points.", {"style": "bullets", "count": 2}),
    ("Can you put that in 3 bullets?", {"style": "bullets", "count": 3}),
    ("In bullet points please", {"style": "bullets", "count": None}),
    ("Show me the answer as a numbered list.", {"style": "numbered", "count": None}),
    ("Rewrite it in three points", {"style": "bullets", "count": 3}),
])
def test_layout_requests_are_presentation_turns(utterance, layout):
    d = decide(utterance)
    assert d.kind == PRESENTATION and d.signals == {"retrieval_required": False, "layout": layout}


@pytest.mark.parametrize("utterance", [
    "What are the UConn travel advance requirements?",
    "What is Rutgers University's Travel Card policy?",
    "What about the submission deadline?",
    "Does that apply currently?",
    "Compare the February 1, 2026 and July 1, 2026 UConn Travel Card penalty rules.",
    "UConn travel advance requirements",  # no question mark, but it ends on a noun: not visibly unfinished
    "what should a traveler do", "Who is it for",
    # a word outside the gate's vocabularies makes it a question
    "Give me the UConn travel advance requirements in two bullet points.",
    "Ok, what about the deadline?", "Thanks. What is the deadline?", "Is that all?",
    "Make it shorter.", "Summarize that in one sentence.", "Give me that again.", "List the points.",
    "What are the three points of the policy?",
])
def test_everything_else_is_a_question_for_the_existing_path(utterance):
    d = decide(utterance)
    assert d.kind == RETRIEVE and d.signals == {"retrieval_required": True}


def test_the_gate_leaves_every_evaluation_question_to_the_existing_path():
    import importlib

    questions = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                questions.append(v) if k in ("question", "query") and isinstance(v, str) else walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                questions.append(v) if isinstance(v, str) and v.endswith("?") else walk(v)

    for name in ("integration", "multi_intent", "session", "streaming", "generation", "retrieval", "temporal"):
        module = importlib.import_module(f"evaluation.{name}.cases")
        for attr in dir(module):
            if attr.isupper():
                walk(getattr(module, attr))
    assert len(questions) > 100 and [q for q in questions if decide(q).kind != RETRIEVE] == []


def test_join_continues_or_replaces_what_was_waiting():
    assert join("What are the Rutgers travel...", "advance requirements?") == "What are the Rutgers travel advance requirements?"
    assert join("What are the Rutgers travel…", "What are the Rutgers travel advance requirements?") == Q_ADV  # sent again, longer
    assert join("What are the", "  Rutgers   rules?") == "What are the Rutgers rules?"


# ---------------------------------------------------------------- wait


def test_an_unfinished_utterance_waits_without_retrieval_or_generation_and_is_kept():
    c = Counted()
    t = c.ask(Q_ADV_HEAD)
    assert c.work == (0, 0) and t.answer is None
    assert (t.resolution.kind, t.status, t.text, t.citations, t.abstention_reason) == (WAIT, "waiting", WAITING_TEXT, [], None)
    assert t.question == Q_ADV_HEAD and t.resolution.query == ""  # nothing went to the pipeline
    assert t.resolution.signals == {"decision": WAIT, "retrieval_required": False, "pending": Q_ADV_HEAD}
    assert decode_session(encode_session(c.session)) == c.session  # the partial utterance is session state


@pytest.mark.parametrize("fragment", [Q_ADV_TAIL, Q_ADV])  # the rest of it, or the whole utterance sent again
def test_the_next_fragment_after_a_wait_resumes_as_the_whole_question(fragment):
    whole = Counted()
    want = whole.ask(Q_ADV)
    c = Counted()
    c.ask(Q_ADV_HEAD)
    t = c.ask(fragment)
    assert t.index == 1 and t.question == Q_ADV and t.resolution.kind == SELF_CONTAINED and t.resolution.query == Q_ADV
    assert t.resolution.signals["continues_turn"] == 0 and t.resolution.signals["decision"] == RETRIEVE
    assert c.retrievals == whole.retrievals and c.provider.calls == whole.provider.calls  # the same work, once
    assert (t.status, t.text, t.citations) == (want.status, want.text, want.citations) and t.status == "answered"


def test_a_wait_can_last_several_fragments_and_a_follow_up_keeps_its_context():
    c = Counted()
    first = c.ask(Q_ADV)
    work = c.work
    a = c.ask("What about the")
    assert (a.resolution.kind, a.resolution.anchor) == (WAIT, first.index) and c.work == work
    b = c.ask("documentation required after the")
    assert b.resolution.kind == WAIT and c.work == work
    assert b.resolution.signals["pending"] == "What about the documentation required after the"
    assert (b.question, b.resolution.signals["continues_turn"]) == ("documentation required after the", a.index)
    t = c.ask("trip?")
    assert t.question == "What about the documentation required after the trip?"
    assert (t.resolution.kind, t.resolution.anchor) == (FOLLOW_UP, 0) and t.status == "answered"  # against the question
    assert SETTLE.chunk_id in t.cited_chunks and len(c.retrievals) == work[0] + 1


def test_an_acknowledgement_closes_an_unfinished_utterance():
    c = Counted()
    c.ask(Q_ADV_HEAD)
    t = c.ask("Ok, thanks.")
    assert t.resolution.kind == SUPPRESS and c.work == (0, 0)
    after = c.ask(Q_LODGE)  # the abandoned fragment is not prepended to a later question
    assert after.question == Q_LODGE and after.resolution.kind == SELF_CONTAINED


# ---------------------------------------------------------------- suppress


def test_an_acknowledgement_is_suppressed_without_retrieval_generation_or_text():
    c = Counted()
    first = c.ask(Q_ADV)
    work = c.work
    t = c.ask(THANKS)
    assert c.work == work and t.answer is None
    assert (t.resolution.kind, t.status, t.text, t.citations, t.abstention_reason) == (SUPPRESS, "suppressed", "", [], None)
    assert t.resolution.signals == {"decision": SUPPRESS, "retrieval_required": False}
    assert t.resolution.anchor == first.index and t.reused_citations == []
    assert c.ask("ok").resolution.kind == SUPPRESS and c.work == work
    assert Counted().ask("Thanks!").resolution.kind == SUPPRESS  # also as a first turn: nothing is retrieved for it


def test_a_follow_up_after_a_suppressed_turn_still_refers_to_the_question():
    c = Counted()
    c.ask(Q_ADV)
    c.ask(THANKS)
    t = c.ask("What about the documentation required after the trip?")
    assert (t.resolution.kind, t.resolution.anchor) == (FOLLOW_UP, 0) and t.status == "answered"
    assert "Earlier in this conversation: What are the Rutgers travel advance requirements" in t.resolution.query


# ---------------------------------------------------------------- presentation


def test_a_layout_request_represents_the_stored_answer_without_retrieval_or_generation():
    c = Counted()
    first = c.ask(Q_ADV)
    assert first.status == "answered" and len(first.answer.claims) >= 2
    work = c.work
    t = c.ask(BULLETS)
    assert c.work == work  # no corpus retrieval, no model call
    assert (t.resolution.kind, t.resolution.anchor, t.status) == (PRESENTATION, first.index, "answered")
    assert t.resolution.signals == {"decision": PRESENTATION, "retrieval_required": False,
                                    "layout": {"style": "bullets", "count": 2}}
    lines = t.text.splitlines()
    assert len(lines) == 2 and all(line.startswith("- ") for line in lines)  # the two bullets that were asked for
    # every verified claim of the stored answer, in order, with its citation numbers; nothing else
    sentences = [cl["text"] + "".join(f"[{n}]" for n in cl["citations"]) for cl in first.answer.claims]
    assert " ".join(line[2:] for line in lines) == " ".join(sentences)
    assert t.citations == first.citations and t.citations and t.answer.claims == first.answer.claims
    assert t.answer.evidence_intents == first.answer.evidence_intents and t.answer.phase1 == first.answer.phase1
    assert t.reused_citations == first.cited_chunks and first.text != t.text
    assert first.answer.text == first.text  # the stored answer is not modified


def test_layouts_never_add_drop_or_reword_a_claim():
    c = Counted()
    first = c.ask(Q_ADV)
    n = len(first.answer.claims)
    numbered = c.ask("As a numbered list please").text.splitlines()
    assert [line.split(". ", 1)[0] for line in numbered] == [str(k) for k in range(1, n + 1)]
    assert len(c.ask("in bullet points").text.splitlines()) == n  # no count: one per claim
    assert len(c.ask("Give me that in ten bullet points").text.splitlines()) == n  # no claim is invented to fill ten
    one = c.ask("Put that in one bullet").text
    assert one.count("\n") == 0 and all(cl["text"] in one for cl in first.answer.claims)
    assert present(first.answer, {"style": "bullets", "count": 2}).citations == first.citations


def test_a_layout_request_presents_the_last_question_even_after_other_non_question_turns():
    c = Counted()
    first = c.ask(Q_ADV)
    c.ask(THANKS)
    c.ask("in bullet points")
    t = c.ask(BULLETS)
    assert t.resolution.anchor == first.index and len(t.text.splitlines()) == 2 and t.citations == first.citations


def test_a_layout_request_with_no_answer_to_lay_out_is_unresolved_not_retrieved():
    c = Counted()
    t = c.ask(BULLETS)
    assert (t.resolution.kind, t.status, t.abstention_reason) == (UNRESOLVED, "abstained", "unresolved_reference")
    assert c.work == (0, 0) and "no answer to lay out" in t.resolution.reason


def test_a_layout_request_after_an_abstention_returns_the_abstention_unchanged():
    c = Counted(lambda system, user: insufficient_out())
    first = c.ask(Q_ADV)
    work = c.work
    t = c.ask(BULLETS)
    assert first.status == "abstained" and c.work == work
    assert (t.resolution.kind, t.status, t.text, t.citations) == (PRESENTATION, "abstained", first.text, [])


# ---------------------------------------------------------------- the existing path


def test_normal_questions_follow_ups_and_context_switches_still_retrieve():
    c = Counted()
    first = c.ask(Q_ADV)
    assert first.resolution.kind == SELF_CONTAINED and c.work == (1, 1)
    assert first.resolution.signals["decision"] == RETRIEVE and first.resolution.signals["retrieval_required"] is True
    follow = c.ask("What about the documentation required after the trip?")
    assert (follow.resolution.kind, follow.resolution.anchor) == (FOLLOW_UP, 0) and c.work == (2, 2)
    switch = c.ask(Q_LODGE)  # names another organization and does not refer back
    assert switch.resolution.kind == SELF_CONTAINED and switch.resolution.organizations == ["Test University"]
    assert c.work == (3, 3) and LODGE.chunk_id in switch.cited_chunks and c.retrievals[-1] == Q_LODGE
    unresolved = c.ask("Does Rutgers have the same rule?")
    assert unresolved.resolution.kind == UNRESOLVED and unresolved.resolution.signals["retrieval_required"] is False


def test_the_gate_does_not_change_a_retrieved_turn():
    plain = Counted()
    want = plain.ask(Q_ADV)
    c = Counted()
    c.ask(THANKS)
    got = c.ask(Q_ADV)
    assert (got.text, got.citations, got.resolution.query) == (want.text, want.citations, want.resolution.query)
    assert c.provider.calls == plain.provider.calls and got.resolution.kind == SELF_CONTAINED  # first question of the session


# ---------------------------------------------------------------- through the job worker and the api


def rig():
    return Rig(stack(*CHUNKS), StubProvider(every_source), ALIASES)


def test_the_job_and_query_contracts_report_the_new_states():
    r = rig()
    first = QueryResponse.model_validate(r.query("s", Q_ADV).json())
    work = (len(r.retrieval.requests), len(r.generation.requests))
    assert first.answer_status == "answered" and work[0] >= 1 and work[1] == 1

    wait = QueryResponse.model_validate(r.query("s", "What about the").json())
    assert (wait.status, wait.answer_status, wait.resolution.kind, wait.text) == ("completed", "waiting", WAIT, WAITING_TEXT)
    assert (wait.citations, wait.evidence, wait.abstention_reason, wait.strategy, wait.resolution.query) == ([], [], None, None, "")

    done = QueryResponse.model_validate(r.query("s", THANKS).json())
    assert (done.status, done.answer_status, done.resolution.kind, done.text) == ("completed", "suppressed", SUPPRESS, "")
    assert (done.citations, done.evidence, done.abstention_reason) == ([], [], None)

    bullets = QueryResponse.model_validate(r.query("s", BULLETS).json())
    assert (bullets.answer_status, bullets.resolution.kind, bullets.resolution.anchor) == ("answered", PRESENTATION, 0)
    assert bullets.citations == first.citations and bullets.evidence == first.evidence
    assert len(bullets.text.splitlines()) == 2 and bullets.reused_citations == [c["chunk_id"] for c in first.citations]

    assert (len(r.retrieval.requests), len(r.generation.requests)) == work  # none of the three touched a service
    stored = r.api.get("/sessions/s").json()["turns"]
    assert [t["resolution"]["kind"] for t in stored] == [SELF_CONTAINED, WAIT, SUPPRESS, PRESENTATION]
    assert [t["resolution"]["signals"]["retrieval_required"] for t in stored] == [True, False, False, False]
    assert [t["status"] for t in stored] == ["answered", "waiting", "suppressed", "answered"]
    result = JobResult.from_turn(r.store.get("s").turns[1])
    assert (result.resolution, result.answer_status, result.abstention_reason, result.citations) == (WAIT, "waiting", None, [])


def test_a_fragment_after_a_wait_resumes_through_the_api():
    r = rig()
    assert r.query("s", Q_ADV_HEAD).json()["answer_status"] == "waiting" and r.retrieval.requests == []
    body = QueryResponse.model_validate(r.query("s", Q_ADV_TAIL).json())
    assert (body.turn_index, body.answer_status, body.resolution.kind, body.resolution.query) == (1, "answered", SELF_CONTAINED, Q_ADV)
    assert body.citations and [q.query for q in r.retrieval.requests] == [Q_ADV]
    assert r.api.get("/sessions/s").json()["turns"][1]["question"] == Q_ADV


def test_the_orchestrator_span_carries_the_decision_and_no_retrieval_or_generation_span_exists():
    r = rig()
    r.query("s", Q_ADV)
    with capture() as spans:
        for utterance in ("What about the", THANKS, BULLETS):
            assert r.query("s", utterance).status_code == 200
    finished = spans.get_finished_spans()
    jobs = [s for s in finished if s.name == "orchestrator.execute"]
    assert [s.attributes["orchestrator.resolution"] for s in jobs] == [WAIT, SUPPRESS, PRESENTATION]
    assert [s.attributes["orchestrator.answer_status"] for s in jobs] == ["waiting", "suppressed", "answered"]
    assert not {s.name for s in finished} & {"POST /retrieve", "retrieval.execute", "POST /generate", "generation.execute"}
