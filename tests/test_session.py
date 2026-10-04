"""Session-aware refinement (phase 3, offline): follow-up resolution rules, session state, temporal refinement, new
retrieval, ambiguous references, citation provenance, and phase 2 unchanged for self-contained questions.

The frozen pipeline runs unchanged over the keyword-overlap stand-ins of tests/test_multi_intent_controller.py; the
stub model answers from the top-ranked source [S1], quoting it verbatim. No network, no models.
"""

import copy

from adaptive.multi.controller import MultiIntentController
from adaptive.multi.decompose import decompose
from adaptive.session.controller import SessionController
from adaptive.session.resolve import compose, references, resolve, strip_temporal, unknown_names
from adaptive.session.state import FOLLOW_UP, SELF_CONTAINED, UNRESOLVED, UNRESOLVED_TEXT, Resolution, Session, Turn
from generation.providers import StubProvider
from temporal.intent import parse_query
from tests.test_generation import answered, mk
from tests.test_generation_verification import FEB_CARD, JUL_CARD, SERIES
from tests.test_multi_intent_controller import label_of, stack

ALIASES = {"TU": "Test University", "Rutgers": "Rutgers University"}
ORGS = {"Test University", "Rutgers University"}

LODGE = mk("lodge", "Travelers booking a multi-bedroom accommodation must use a personal credit card.", doc="tu-lodging")
ADVANCE = mk("adv", "Travel advance requests must be submitted 4-6 weeks prior to departure.", org="Rutgers University",
             doc="ru")
SETTLE = mk("settle", "After the trip, documentation with itemized receipts is required to settle the travel advance.",
            org="Rutgers University", doc="ru")
CHUNKS = [FEB_CARD, JUL_CARD, LODGE, ADVANCE, SETTLE]

Q_LODGE = "What is TU's rule for a multi-bedroom accommodation?"
Q_ADVANCE = "When must Rutgers travel advance requests be submitted?"
Q_FEB = "What did TU's February 2026 procedures say about card charges not submitted?"
Q_TWO = "When must Rutgers travel advance requests be submitted, and what is TU's rule for a multi-bedroom accommodation?"


def top_source(system, user):
    for c in CHUNKS:
        if label_of(user, c) == "S1":
            return answered((c.text, ["S1"], [c.text]))
    return {"status": "insufficient_evidence", "claims": [], "not_in_sources": [], "abstention_reason": "none"}


def setup():
    provider = StubProvider(top_source)
    return SessionController(stack(*CHUNKS), provider, ALIASES), provider, Session()


def turn(kind, question="q", topic=None, temporal="", orgs=(), index=0):
    """A recorded turn for the resolution rules (no answer: one question, or none when unresolved)."""
    r = Resolution(kind, "", question, topic or [question], temporal, list(orgs))
    return Turn(index, question, r, None)


# ---------------------------------------------------------------- resolution rules (pure)


def test_first_turn_is_self_contained_and_verbatim():
    r = resolve("  Does that apply currently?  ", None, ORGS, ALIASES)
    assert r.kind == SELF_CONTAINED and r.query == "  Does that apply currently?  " and r.anchor is None


def test_references_and_unknown_names():
    assert references("Does that apply currently?") == ["Does that"]
    assert references("Is this policy still in effect?") == ["Is this", "this policy"]
    assert references("What about lodging?") == ["What about"]
    assert references("What is the rule that covers tips at Rutgers?") == []  # a relative "that" is not a reference
    assert unknown_names("What does Harvard's policy say?") == ["Harvard"]
    assert unknown_names("What applied as of February 2026?") == []
    assert unknown_names("What is the purpose of the Travel Card?", "the University Travel Card rule") == []


def test_strip_temporal_removes_dates_and_current_only():
    assert strip_temporal("Under TU's procedures effective July 1, 2026, how is a card reinstated?") == \
        "Under TU's procedures, how is a card reinstated?"
    assert strip_temporal("What did TU's February 2026 procedures say?") == "What did TU's procedures say?"
    assert strip_temporal("Does that apply under the current procedures?") == "Does that apply under the procedures?"
    assert parse_query(strip_temporal("What is the rule now, and in February 2026?")).kind == "neutral"


def test_composed_query_is_one_question_for_phase_2_with_the_topic_as_context():
    q = compose("does that apply currently?", [Q_FEB])
    assert q == ("(Earlier in this conversation: What did TU's procedures say about card charges not submitted). "
                 "Does that apply currently?")
    d = decompose(q, ORGS, ALIASES)
    assert not d.multi and d.intents[0].organizations == ["Test University"]
    two = decompose(compose("Does this policy apply currently, and what documentation is required after the trip?",
                            [Q_ADVANCE]), ORGS, ALIASES)
    assert two.multi and all(i.sub_query.startswith("(Earlier in this conversation: When must Rutgers")
                             for i in two.intents)


def test_follow_up_temporal_constraint_overrides_and_is_inherited():
    t0 = turn(SELF_CONTAINED, Q_FEB, temporal="February 2026", orgs=["Test University"])
    own = resolve("Does that apply currently?", t0, ORGS, ALIASES)
    assert own.kind == FOLLOW_UP and parse_query(own.query).kind == "current" and own.temporal == "currently"
    kept = resolve("What happens after 90 days?", t0, ORGS, ALIASES)
    assert kept.query.endswith("; version: February 2026). What happens after 90 days?")
    assert parse_query(kept.query).kind == "point_in_time" and kept.temporal == "February 2026"


def test_follow_up_is_read_with_the_topic_start_and_the_previous_question():
    t0 = turn(SELF_CONTAINED, Q_ADVANCE, orgs=["Rutgers University"])
    t1 = Turn(1, "What documentation is required after the trip?",
              resolve("What documentation is required after the trip?", t0, ORGS, ALIASES), None)
    r = resolve("Is that required currently?", t1, ORGS, ALIASES)
    assert r.topic == [Q_ADVANCE, "What documentation is required after the trip?"] and r.anchor == 1
    assert r.organizations == ["Rutgers University"]


def test_self_contained_and_unresolvable_follow_ups():
    t0 = turn(SELF_CONTAINED, Q_LODGE, orgs=["Test University"])
    assert resolve(Q_ADVANCE, t0, ORGS, ALIASES).kind == SELF_CONTAINED  # names its own organization
    assert resolve("Does that TU rule apply currently?", t0, ORGS, ALIASES).kind == FOLLOW_UP  # same organization
    other = resolve("Does Rutgers have the same rule?", t0, ORGS, ALIASES)
    assert other.kind == UNRESOLVED and "names Rutgers University" in other.reason and other.query == ""
    outside = resolve("Does Harvard University have that policy?", t0, ORGS, ALIASES)
    assert outside.kind == UNRESOLVED and "Harvard University" in outside.reason
    two_orgs = turn(SELF_CONTAINED, "q", orgs=["Test University", "Rutgers University"])
    assert resolve("Does that apply currently?", two_orgs, ORGS, ALIASES).kind == UNRESOLVED
    after = resolve("And what about receipts?", turn(UNRESOLVED), ORGS, ALIASES)
    assert after.kind == UNRESOLVED and "previous turn could not be resolved" in after.reason


# ---------------------------------------------------------------- session controller


def test_first_question_creates_session_state():
    ctl, provider, session = setup()
    t = ctl.ask(session, Q_LODGE)
    assert session.turns == [t] and session.last is t and t.index == 0
    assert t.resolution.kind == SELF_CONTAINED and t.resolution.topic == [Q_LODGE]
    assert t.resolution.organizations == ["Test University"] and t.intents == 1
    assert t.status == "answered" and t.cited_chunks == [LODGE.chunk_id] and len(provider.calls) == 1
    assert session.to_dict()["turns"][0]["resolution"]["kind"] == SELF_CONTAINED


def test_self_contained_second_question_is_exactly_phase_2():
    ctl, provider, session = setup()
    ctl.ask(session, Q_LODGE)
    t = ctl.ask(session, Q_ADVANCE)
    solo = StubProvider(top_source)
    a = MultiIntentController(stack(*CHUNKS), solo, ALIASES).run(Q_ADVANCE)
    assert t.resolution.kind == SELF_CONTAINED and t.resolution.query == Q_ADVANCE
    assert provider.calls[1:] == solo.calls and t.answer.text == a.text and t.cited_chunks == [ADVANCE.chunk_id]


def test_reference_follow_up_uses_prior_context():
    ctl, provider, session = setup()
    ctl.ask(session, Q_LODGE)
    t = ctl.ask(session, "Does this policy allow a personal credit card?")
    assert t.resolution.kind == FOLLOW_UP and t.resolution.anchor == 0
    assert t.resolution.query.startswith("(Earlier in this conversation: What is TU's rule for a multi-bedroom")
    assert provider.calls[1][1].startswith(f"Question: {t.resolution.query}\n")  # the model sees the resolved question
    assert t.cited_chunks == [LODGE.chunk_id] and t.reused_citations == [LODGE.chunk_id]


def test_temporal_follow_up_selects_the_current_version():
    ctl, provider, session = setup()
    t0 = ctl.ask(session, Q_FEB)
    assert t0.answer.intents[0]["selected_versions"] == {SERIES: ["proc-feb"]}
    assert t0.cited_chunks == [FEB_CARD.chunk_id]
    t1 = ctl.ask(session, "Does that apply currently?")
    assert t1.resolution.kind == FOLLOW_UP and t1.resolution.temporal == "currently"
    assert t1.answer.intents[0]["temporal_intent"] == "current"
    assert t1.answer.intents[0]["selected_versions"] == {SERIES: ["proc-jul"]}
    assert t1.cited_chunks == [JUL_CARD.chunk_id] and t1.reused_citations == []
    assert FEB_CARD.chunk_id not in provider.calls[1][1]  # the other version never reaches the model


def test_follow_up_with_a_new_constraint_retrieves_new_evidence():
    ctl, _, session = setup()
    t0 = ctl.ask(session, Q_ADVANCE)
    t1 = ctl.ask(session, "What documentation is required after the trip?")
    assert t1.resolution.kind == FOLLOW_UP and t1.resolution.reason == "names no organization"
    assert t0.cited_chunks == [ADVANCE.chunk_id] and t1.cited_chunks == [SETTLE.chunk_id]
    assert t1.reused_citations == []


def test_ambiguous_follow_up_abstains_without_retrieval_or_model_call():
    ctl, provider, session = setup()
    t0 = ctl.ask(session, Q_TWO)
    assert t0.intents == 2
    calls = len(provider.calls)
    t1 = ctl.ask(session, "Does that apply currently?")
    assert t1.resolution.kind == UNRESOLVED and "asked 2 questions" in t1.resolution.reason
    assert t1.answer is None and t1.status == "abstained" and t1.text == UNRESOLVED_TEXT and t1.citations == []
    t2 = ctl.ask(session, "What about receipts?")
    assert t2.resolution.kind == UNRESOLVED and len(provider.calls) == calls


def test_follow_up_naming_another_organization_is_not_given_the_topic():
    ctl, provider, session = setup()
    ctl.ask(session, Q_LODGE)
    t = ctl.ask(session, "Does Rutgers have the same rule?")
    assert t.resolution.kind == UNRESOLVED and t.answer is None and len(provider.calls) == 1


def test_citations_stay_with_their_own_turn():
    ctl, provider, session = setup()
    t0 = ctl.ask(session, Q_ADVANCE)
    before = copy.deepcopy(t0.to_dict())
    t1 = ctl.ask(session, "What documentation is required after the trip?")
    assert t0.to_dict() == before  # a later turn never changes an earlier one
    shown = {s["chunk_id"] for a in t1.answer.phase1 for s in a.sources_considered}
    assert set(t1.cited_chunks) <= shown  # every citation comes from this turn's own retrieval
    assert [c.number for c in t1.citations] == [1] and t1.answer.claims[0]["citations"] == [1]
