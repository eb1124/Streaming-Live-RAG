"""Late-constraint refinement (offline): "refine, do not restart". A constraint added to an answered question gets one
targeted retrieval for its delta and an answer v2 merged from the stored answer v1 and the delta's answer; ordinary
follow-ups, context switches and the turn gate are unchanged. Asserted through the session controller, the job
worker and the api; no network, no models (the keyword stand-ins of tests/test_multi_intent_controller.py).
"""

import copy
from dataclasses import replace

import pytest

from adaptive.session.controller import REFINE, SessionController
from adaptive.session.refine import REFINED, merge, same_statement
from adaptive.session.resolve import late_constraint, new_words, resolve, topic_phrase
from adaptive.session.state import (FOLLOW_UP, PRESENTATION, REFINEMENT, SELF_CONTAINED, SUPPRESS, UNRESOLVED, WAIT,
                                    Session)
from adaptive.session_store.codec import decode_session, encode_session
from generation.providers import StubProvider
from services.contracts import QueryResponse
from services.telemetry.testing import capture
from tests.test_generation import answered, mk
from tests.test_generation_verification import FEB_CARD, JUL_CARD, SERIES
from tests.test_multi_intent_controller import insufficient_out, label_of, question_of, stack
from tests.test_services import Rig
from tests.test_session import ALIASES, ORGS, turn

GEN = mk("gen", "Employee trip expenses are reimbursed after an expense report with receipts is submitted.")
MEAL = mk("meal", "Meals for an employee trip are reimbursed at the domestic per diem rate of 60 dollars.")
LODGE = mk("lodge", "Lodging for an employee trip requires an itemized hotel receipt.")
INTL_MEAL = mk("imeal", "Meals for an international employee trip are reimbursed at the foreign per diem rate of 90 dollars.")
INTL_OK = mk("iok", "International travel requires approval by the global office before departure.")
ADVANCE = mk("adv", "Travel advance requests must be submitted 4-6 weeks prior to departure.", org="Rutgers University",
             doc="ru")
CHUNKS = [GEN, MEAL, LODGE, INTL_MEAL, INTL_OK, ADVANCE, FEB_CARD, JUL_CARD]
BROAD, INTERNATIONAL = (GEN, MEAL, LODGE), (INTL_MEAL, INTL_OK)

Q1 = "What are the travel reimbursement rules for an employee trip?"
LATE = "Actually, the trip was international."
DELTA_QUERY = "international travel reimbursement rules for an employee trip"
REFINED_QUESTION = f"(Earlier in this conversation: the trip was international). {Q1}"


def model(system, user):
    """Answers the question it is asked from the sources it is shown: the international rules when the question
    says the trip was international, the general ones otherwise; card and advance questions from their chunk."""
    q = question_of(user)
    wanted = INTERNATIONAL if "international" in q else (FEB_CARD, JUL_CARD) if "card charges" in q else (
        (ADVANCE,) if "advance" in q else BROAD)
    claims = [(c.text, [label_of(user, c)], [c.text]) for c in wanted if label_of(user, c)]
    return answered(*claims) if claims else insufficient_out()


class Conversation:
    def __init__(self, script=model):
        self.provider = StubProvider(script)
        self.controller = SessionController(stack(*CHUNKS), self.provider, ALIASES)
        self.retrievals = []
        base = self.controller.multi.retrieve
        self.controller.multi.retrieve = lambda st, q, k=10: self.retrievals.append(q) or base(st, q, k)
        self.session = Session()

    def ask(self, question):
        return self.controller.ask(self.session, question)


def cited(t, text):
    """The chunk ids a claim of the turn cites."""
    by_number = {c.number: c.chunk_id for c in t.citations}
    return [by_number[n] for cl in t.answer.claims if cl["text"] == text for n in cl["citations"]]


# ---------------------------------------------------------------- the decision (pure)


@pytest.mark.parametrize("utterance, constraint, delta", [
    ("Actually, the trip was international.", "the trip was international", "international"),
    ("This was for international travel.", "This was for international travel", "international"),
    ("I meant domestic travel.", "domestic travel", "domestic"),
    ("The employee was traveling internationally.", "The employee was traveling internationally", "traveling internationally"),
    ("Use the July 2026 version instead.", "Use the July 2026 version", "July 2026"),
    ("Sorry, actually, I meant a student trip", "a student trip", "student"),
    ("For international travel only.", "For international travel", "international"),
])
def test_a_late_constraint_and_its_delta(utterance, constraint, delta):
    assert late_constraint(utterance) == constraint
    assert new_words(constraint, [Q1]) == delta
    r = resolve(utterance, turn(SELF_CONTAINED, Q1), ORGS, ALIASES)
    assert (r.kind, r.anchor, r.signals["constraint"], r.signals["delta"]) == (REFINEMENT, 0, constraint, delta)


@pytest.mark.parametrize("utterance", [
    "What about the submission deadline?", "Does that apply currently?", "And when is it paid?",
    "Is the trip international?", "Tell me about lodging.", "Explain the meal rules.", "Lodging receipts",
    "Compare the February 1, 2026 and July 1, 2026 rules.",
])
def test_questions_and_requests_are_not_late_constraints(utterance):
    assert late_constraint(utterance) is None
    assert resolve(utterance, turn(SELF_CONTAINED, Q1), ORGS, ALIASES).kind != REFINEMENT


def test_a_constraint_needs_an_asked_question_the_same_organization_and_something_new():
    assert resolve(LATE, None, ORGS, ALIASES).kind == SELF_CONTAINED  # nothing to refine: the first turn
    assert resolve(LATE, turn(UNRESOLVED, Q1), ORGS, ALIASES).kind == UNRESOLVED
    assert resolve("The trip was for an employee.", turn(SELF_CONTAINED, Q1), ORGS, ALIASES).kind == FOLLOW_UP  # no delta
    tu = turn(SELF_CONTAINED, "What is TU's rule for meals?", orgs=["Test University"])
    assert resolve("Actually, I meant Rutgers.", tu, ORGS, ALIASES).kind == SELF_CONTAINED  # another organization: a switch
    assert resolve("Actually, this was at TU.", turn(SELF_CONTAINED, Q1), ORGS, ALIASES).organizations == ["Test University"]
    assert resolve("Actually, the TU trip was international.", tu, ORGS, ALIASES).kind == REFINEMENT  # the same one


def test_the_refined_question_and_the_delta_query():
    r = resolve(LATE, turn(SELF_CONTAINED, Q1), ORGS, ALIASES)
    assert r.query == REFINED_QUESTION  # the question that was asked, the constraint as its context
    assert r.signals["retrieval_query"] == DELTA_QUERY  # the new words, then the topic: not the question replayed
    assert Q1 not in r.signals["retrieval_query"] and "Earlier" not in r.signals["retrieval_query"]
    assert r.topic == [Q1, "the trip was international"] and r.signals["questions"] == 1
    assert topic_phrase("How long do TU travelers have to submit charges?") == "How long do TU travelers have to submit charges"


def test_a_temporal_constraint_replaces_the_version_in_the_delta_query():
    q = "What did TU's February 2026 procedures say about card charges not submitted?"
    r = resolve("Use the July 2026 version instead.", turn(SELF_CONTAINED, q, temporal="February 2026",
                                                           orgs=["Test University"]), ORGS, ALIASES)
    assert (r.kind, r.temporal) == (REFINEMENT, "July 2026")
    assert r.signals["retrieval_query"] == "July 2026 What did TU's procedures say about card charges not submitted"
    assert "February" not in r.query and "July 2026" in r.query


def test_no_turn_of_the_session_evaluation_is_a_late_constraint():
    from evaluation.session import cases

    turns = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                turns.append(v) if k == "question" and isinstance(v, str) else walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    walk(cases.CONVERSATIONS)
    assert len(turns) > 10 and [q for q in turns if late_constraint(q)] == []


def test_same_statement():
    assert same_statement(MEAL.text, INTL_MEAL.text)  # the same rule with another rate
    assert same_statement("Charges must be submitted within 30 days.", "Charges must be submitted within 60 days.")
    assert not same_statement(GEN.text, INTL_MEAL.text) and not same_statement(LODGE.text, INTL_OK.text)


# ---------------------------------------------------------------- A, B, C: refine, do not restart


def test_a_late_constraint_refines_the_stored_answer_with_one_targeted_retrieval():
    c = Conversation()
    v1 = c.ask(Q1)
    assert v1.status == "answered" and [cl["text"] for cl in v1.answer.claims] == [x.text for x in BROAD]
    before = copy.deepcopy(v1)
    v2 = c.ask(LATE)
    assert (v2.resolution.kind, v2.resolution.anchor, v2.status) == (REFINEMENT, v1.index, "answered")
    assert v2.answer.strategy == REFINED and v2.resolution.query == REFINED_QUESTION
    # one retrieval, for the delta: neither the question again nor the conversation rewritten as a new question
    assert c.retrievals == [Q1, DELTA_QUERY] and len(c.provider.calls) == 2
    assert question_of(c.provider.calls[1][1]).endswith(REFINED_QUESTION)  # the model is asked the question, refined
    s = v2.resolution.signals
    assert (s["decision"], s["retrieval_required"], s["retrieval_query"], s["delta"]) == (REFINE, True, DELTA_QUERY, "international")
    assert v1 == before and c.session.turns[0] is v1  # answer v1 is still the session's, unchanged
    assert decode_session(encode_session(c.session)) == c.session


# ---------------------------------------------------------------- D, E, F: what is kept, replaced, cited


def test_valid_claims_are_kept_a_conflicting_one_is_replaced_and_citations_follow_the_claims():
    c = Conversation()
    v1 = c.ask(Q1)
    v2 = c.ask(LATE)
    texts = [cl["text"] for cl in v2.answer.claims]
    # kept where they stood; the international meal rate stands where the domestic one stood; the new rule is added
    assert texts == [GEN.text, INTL_MEAL.text, LODGE.text, INTL_OK.text]
    assert [cl["refinement"] for cl in v2.answer.claims] == ["kept", "replaces", "kept", "new"]
    assert MEAL.text not in v2.text and "60 dollars" not in v2.text and "90 dollars" in v2.text  # never both
    record = v2.resolution.signals["refinement"]
    assert record["kept"] == [GEN.text, LODGE.text] and record["added"] == [INTL_OK.text] and record["dropped"] == []
    assert record["replaced"] == [{"claim": MEAL.text, "by": INTL_MEAL.text}]
    # citations: a kept claim cites what it cited in v1; a changed or new claim cites the newly retrieved evidence
    for text in (GEN.text, LODGE.text):
        assert cited(v2, text) == cited(v1, text) and cited(v2, text)
    assert cited(v2, INTL_MEAL.text) == [INTL_MEAL.chunk_id] and cited(v2, INTL_OK.text) == [INTL_OK.chunk_id]
    assert MEAL.chunk_id not in v2.cited_chunks and [x.number for x in v2.citations] == [1, 2, 3, 4]
    assert v2.text.count("[") == 4 and all(f"[{n}]" in v2.text for n in (1, 2, 3, 4))
    assert v2.reused_citations == [GEN.chunk_id, LODGE.chunk_id]
    assert record["evidence"] == {"preserved": [GEN.chunk_id, LODGE.chunk_id], "new": [INTL_MEAL.chunk_id, INTL_OK.chunk_id]}
    assert v2.answer.citation_intents == {1: [0], 2: [0], 3: [0], 4: [0]} and v2.answer.intents[0]["citations"] == [1, 2, 3, 4]


def test_a_claim_whose_version_the_constraint_deselects_is_dropped_not_inherited():
    c = Conversation()
    v1 = c.ask("What did TU's February 2026 procedures say about card charges not submitted?")
    assert v1.cited_chunks == [FEB_CARD.chunk_id]
    v2 = c.ask("Use the July 2026 version instead.")
    assert v2.resolution.kind == REFINEMENT and v2.answer.intents[0]["selected_versions"] == {SERIES: ["proc-jul"]}
    assert v2.cited_chunks == [JUL_CARD.chunk_id] and [cl["text"] for cl in v2.answer.claims] == [JUL_CARD.text]
    record = v2.resolution.signals["refinement"]
    dropped = record["dropped"] + record["replaced"]
    assert len(dropped) == 1 and dropped[0]["claim"] == FEB_CARD.text and FEB_CARD.text not in v2.text


def test_a_prior_claim_is_not_inherited_unless_the_verifier_accepts_it_on_the_merged_evidence():
    c = Conversation()
    v1 = c.ask(Q1).answer
    chunks = {x.chunk_id: x for x in CHUNKS}
    tampered = replace(v1, claims=[v1.claims[0] | {"text": "Employee trips are reimbursed within 5 days.",
                                                   "quotes": ["reimbursed within 5 days"]}, *v1.claims[1:]])
    delta = c.controller.multi.answer(REFINED_QUESTION, c.controller.multi.aligned(c.controller.multi.stack, DELTA_QUERY),
                                      REFINED, "test")
    v2, record = merge(tampered, delta, chunks, "the trip was international")
    assert "within 5 days" not in v2.text and LODGE.text in v2.text
    assert [d["claim"] for d in record["dropped"]] == ["Employee trips are reimbursed within 5 days."]
    assert "not verified on the merged evidence" in record["dropped"][0]["why"]
    gone = {k: v for k, v in chunks.items() if k != LODGE.chunk_id}  # its source left the corpus
    v3, record3 = merge(v1, delta, gone, "the trip was international")
    assert LODGE.text not in v3.text and any("not in the corpus" in d["why"] for d in record3["dropped"])


def test_when_the_constraint_finds_nothing_the_valid_claims_stand_and_the_gap_is_stated():
    def nothing_international(system, user):
        return insufficient_out() if "international" in question_of(user) else model(system, user)

    c = Conversation(nothing_international)
    v1 = c.ask(Q1)
    v2 = c.ask(LATE)
    assert v2.resolution.kind == REFINEMENT and v2.status == "answered" and c.retrievals == [Q1, DELTA_QUERY]
    assert [cl["text"] for cl in v2.answer.claims] == [cl["text"] for cl in v1.answer.claims]
    assert v2.answer.not_in_sources == ["anything specific to the refinement (the trip was international)"]
    assert v2.text.endswith("anything specific to the refinement (the trip was international).")
    assert v2.cited_chunks == v1.cited_chunks


def test_refining_an_abstention_is_the_constraints_own_answer():
    def only_international(system, user):
        return model(system, user) if "international" in question_of(user) else insufficient_out()

    c = Conversation(only_international)
    assert c.ask(Q1).status == "abstained"
    v2 = c.ask(LATE)
    assert v2.resolution.kind == REFINEMENT and v2.status == "answered"
    assert [cl["refinement"] for cl in v2.answer.claims] == ["new", "new"] and v2.cited_chunks == [INTL_MEAL.chunk_id, INTL_OK.chunk_id]


def test_a_follow_up_after_a_refinement_keeps_the_question_and_its_constraint():
    c = Conversation()
    c.ask(Q1)
    c.ask(LATE)
    t = c.ask("What about approval?")
    assert (t.resolution.kind, t.resolution.anchor) == (FOLLOW_UP, 1)
    assert t.resolution.query == f"(Earlier in this conversation: {Q1[:-1]}; the trip was international). What about approval?"



def test_a_second_constraint_refines_the_refined_answer():
    c = Conversation()
    c.ask(Q1)
    v2 = c.ask(LATE)
    v3 = c.ask("I meant a student trip.")
    assert (v3.resolution.kind, v3.resolution.anchor) == (REFINEMENT, v2.index)
    assert v3.resolution.signals["retrieval_query"] == "student international travel reimbursement rules for an employee trip"
    assert v3.resolution.topic == [Q1, "the trip was international", "a student trip"]
    assert c.retrievals[-1] == v3.resolution.signals["retrieval_query"] and len(c.retrievals) == 3


# ---------------------------------------------------------------- G, H, I: what does not change


def test_an_ordinary_follow_up_still_takes_the_session_aware_retrieval_path():
    c = Conversation()
    c.ask("When must Rutgers travel advance requests be submitted?")
    t = c.ask("What about the submission deadline?")
    assert (t.resolution.kind, t.resolution.anchor) == (FOLLOW_UP, 0) and "refinement" not in t.resolution.signals
    assert t.resolution.query == ("(Earlier in this conversation: When must Rutgers travel advance requests be submitted). "
                                  "What about the submission deadline?")
    assert c.retrievals[-1] == t.resolution.query and t.answer.strategy != REFINED  # the rewritten query is retrieved
    assert t.resolution.signals["decision"] == "retrieve"


def test_an_explicit_context_switch_is_still_a_new_retrieval():
    c = Conversation()
    c.ask(Q1)
    q = "When must Rutgers travel advance requests be submitted?"
    t = c.ask(q)
    assert (t.resolution.kind, t.resolution.anchor, t.resolution.organizations) == (SELF_CONTAINED, None, ["Rutgers University"])
    assert c.retrievals[-1] == q and t.cited_chunks == [ADVANCE.chunk_id] and t.answer.strategy != REFINED


def test_wait_suppress_and_presentation_are_unchanged_around_a_refinement():
    c = Conversation()
    c.ask(Q1)
    work = (len(c.retrievals), len(c.provider.calls))
    assert c.ask("Actually, the trip was...").resolution.kind == WAIT  # an unfinished constraint waits
    assert c.ask("Thanks, that's all.").resolution.kind == SUPPRESS
    assert (len(c.retrievals), len(c.provider.calls)) == work
    v2 = c.ask(LATE)  # still refines the question, across the turns that were not questions
    assert (v2.resolution.kind, v2.resolution.anchor) == (REFINEMENT, 0) and c.retrievals[-1] == DELTA_QUERY
    work = (len(c.retrievals), len(c.provider.calls))
    bullets = c.ask("Give me that in two bullet points.")
    assert (bullets.resolution.kind, bullets.resolution.anchor) == (PRESENTATION, v2.index)
    assert (len(c.retrievals), len(c.provider.calls)) == work and bullets.citations == v2.citations
    assert len(bullets.text.splitlines()) == 2 and all(cl["text"] in bullets.text for cl in v2.answer.claims)
    continued = Conversation()
    continued.ask(Q1)
    assert continued.ask("Actually, the trip was...").resolution.kind == WAIT
    t = continued.ask("international.")  # a constraint completed over two utterances
    assert (t.resolution.kind, t.question) == (REFINEMENT, "Actually, the trip was international.")


# ---------------------------------------------------------------- through the job worker and the api


def test_the_refinement_is_observable_through_the_api_and_its_span():
    r = Rig(stack(*CHUNKS), StubProvider(model), ALIASES)
    first = QueryResponse.model_validate(r.query("s", Q1).json())
    with capture() as spans:
        body = QueryResponse.model_validate(r.query("s", LATE).json())
    assert (body.status, body.answer_status, body.resolution.kind, body.resolution.anchor) == ("completed", "answered", REFINEMENT, 0)
    assert body.strategy == REFINED and body.resolution.query == REFINED_QUESTION
    assert [q.query for q in r.retrieval.requests] == [Q1, DELTA_QUERY] and len(r.generation.requests) == 2
    kept = [c for c in first.citations if c["chunk_id"] in (GEN.chunk_id, LODGE.chunk_id)]
    assert {c["chunk_id"] for c in kept} <= {c["chunk_id"] for c in body.citations} and body.reused_citations == [
        GEN.chunk_id, LODGE.chunk_id]
    stored = r.api.get("/sessions/s").json()["turns"]
    assert stored[0]["text"] == first.text  # answer v1 is still in the session
    signals = stored[1]["resolution"]["signals"]
    assert (signals["decision"], signals["retrieval_required"], signals["retrieval_query"]) == (REFINE, True, DELTA_QUERY)
    assert signals["refinement"]["replaced"] == [{"claim": MEAL.text, "by": INTL_MEAL.text}]
    assert [cl["refinement"] for cl in stored[1]["answer"]["claims"]] == ["kept", "replaces", "kept", "new"]
    job = [s for s in spans.get_finished_spans() if s.name == "orchestrator.execute"]
    assert [s.attributes["orchestrator.resolution"] for s in job] == [REFINEMENT]
    assert len([s for s in spans.get_finished_spans() if s.name == "retrieval.execute"]) == 1  # one targeted retrieval
