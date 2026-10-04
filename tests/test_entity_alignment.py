"""Entity alignment of retrieved evidence (offline): the named thing a question asks about, the evidence that names
only another one, and the pool's chunks that name the asked one. No network, no models: the keyword stand-ins of
tests/test_multi_intent_controller.py for dense + BM25 + RRF and the cross-encoder.
"""

import pytest

from adaptive.multi.controller import DELEGATE, FUSED, MultiIntentController
from adaptive.multi.decompose import load_aliases
from adaptive.multi.entity import MIN_NAMING, WIDE_K, Entity, align, contested, entities, keep, names, names_other, scarce
from generation import config as C
from generation.pipeline import RETRIEVE_K, retrieve
from generation.providers import StubProvider
from tests.test_generation import answered, ev, mk
from tests.test_multi_intent_controller import insufficient_out, label_of, question_of, stack

ALIASES = load_aliases()
ORGS = set(ALIASES.values()) | {"McGill University", "University of Michigan"}
TRAVEL, PCARD_E = Entity("Travel Card", "travel", "card"), Entity("PCard", "p", "card", True)


# ---------------------------------------------------------------- what a question names


@pytest.mark.parametrize("q, expected", [
    ("What is the Rutgers University Travel Card suspension rule?", ["Travel Card"]),
    ("What are Rutgers University's Travel Card rules?", ["Travel Card"]),
    ("What is UConn's University Travel Card suspension rule?", ["Travel Card"]),  # the last two words of the name
    ("What is the Rutgers University PCard suspension rule?", ["PCard"]),
    ("Which Travel Card and P-Card rules apply at Stanford?", ["Travel Card", "P-Card"]),
    # organizations and institutions are not entities; neither is a term written without capitals
    ("What are UConn's travel advance requirements?", []),
    ("What is McGill University's non-travel advance policy?", []),
    ("What does Boston College pay for tips at Oregon State University?", []),
    ("what are rutgers travel card rules?", []),
    ("How often must UT Austin user accounts be reviewed?", []),
])
def test_entities_named_by_a_question(q, expected):
    assert [e.text for e in entities(q, ORGS, ALIASES)] == expected


def test_an_entity_is_a_modifier_and_a_head():
    assert entities("What are Rutgers University's Travel Cards rules?", ORGS, ALIASES) == [
        Entity("Travel Cards", "travel", "card")]
    assert entities("Is a P-Card allowed?", ORGS, ALIASES) == [Entity("P-Card", "p", "card", True)]


@pytest.mark.parametrize("entity, text, same, other", [
    (TRAVEL, "Travel Card: An employee travel card for university business travel.", True, []),
    (TRAVEL, "the departmental purchasing card (PCard) or employee travel card (TCard)", True, ["PCard"]),
    (TRAVEL, "may be purchased with a TCard.", True, []),  # the cased compound of its initial and head
    (TRAVEL, "may result in the suspension and/or revocation of the employee’s PCard.", False, ["PCard"]),
    (TRAVEL, "The Purchasing Card and the Hosting Card are issued by Procurement.", False, ["Purchasing Card", "Hosting Card"]),
    (TRAVEL, "Pay with a personal credit card; the card must be in the traveler's name.", False, []),  # not names
    (TRAVEL, "Each Card is reviewed. The Card may be suspended.", False, []),
    (PCARD_E, "may result in the suspension and/or revocation of the employee’s PCard.", True, []),
    (PCARD_E, "The purchase of personal items on a university purchasing card is prohibited.", True, []),
    (PCARD_E, "Travel Card: An employee travel card for university business travel.", False, ["Travel Card"]),
    (PCARD_E, "items that can be purchased with a PCard or TCard", True, ["TCard"]),
])
def test_what_a_text_names(entity, text, same, other):
    assert names(entity, text) is same and names_other(entity, text) == other


# ---------------------------------------------------------------- a corpus where the asked entity is scarce

RHO = "Rho University"
PCARD = mk("pcard", "The suspension rule: excessive personal purchases lead to suspension of the employee's PCard, "
           "the university purchasing card.", org=RHO, doc="doc-rho")
DEFN = mk("defn", "Travel Card rule: an employee travel card for university business travel.", org=RHO, doc="doc-rho")
NOTES = [mk(f"n{i}", f"Travel suspension rule note n{i} for university trips.", org=RHO, doc="doc-rho")
         for i in range(10)]
TADV = mk("tadv", "An advance is provided when the employee travel card (TCard) is not accepted. Items that can be "
          "bought with a TCard are excluded from advances.", org=RHO, doc="doc-rho")
RHO_STACK = (PCARD, DEFN, *NOTES, TADV)  # TADV: beyond the top RETRIEVE_K for the Travel Card question
Q_TRAVEL = "What is the Rho University Travel Card suspension rule?"
Q_PCARD = "What is the Rho University PCard suspension rule?"
PCARD_QUOTE = "excessive personal purchases lead to suspension of the employee's PCard"


def rho_script(relabel=False):
    """Says of each source what it says. relabel: states the PCard rule as the Travel Card's, citing whatever source
    is first in the prompt, with the PCard sentence as its quote."""

    def script(system, user):
        claims = []
        p, d, t = label_of(user, PCARD), label_of(user, DEFN), label_of(user, TADV)
        if relabel:
            claims.append(("The Travel Card is suspended for excessive personal purchases", [p or d or "S1"], [PCARD_QUOTE]))
        elif p:
            claims.append(("The PCard is suspended for excessive personal purchases", [p], [PCARD_QUOTE]))
        if d and not relabel:
            claims.append(("A Travel Card is an employee travel card for university business travel",
                           [d], ["an employee travel card for university business travel"]))
        if t and not relabel:
            claims.append(("Items that can be bought with a TCard are excluded from advances", [t],
                           ["Items that can be bought with a TCard are excluded from advances"]))
        return answered(*claims) if claims else insufficient_out()

    return script


def controller(script, chunks=RHO_STACK):
    stub = StubProvider(script)
    return MultiIntentController(stack(*chunks), stub, ALIASES), stub


def ids(evidence):
    return [e.chunk.chunk_id for e in evidence]


def test_the_frozen_retrieval_ranks_the_other_entity_first_and_misses_the_abbreviated_one():
    raw = retrieve(stack(*RHO_STACK), Q_TRAVEL)
    assert len(raw.evidence) == RETRIEVE_K and ids(raw.evidence)[:2] == [PCARD.chunk_id, DEFN.chunk_id]
    assert TADV.chunk_id not in ids(raw.evidence)


def test_a_travel_card_question_is_never_shown_pcard_only_evidence():
    ctl, stub = controller(rho_script())
    a = ctl.run(Q_TRAVEL)
    assert not a.decomposition["multi"] and a.strategy == DELEGATE and len(stub.calls) == 1
    user = stub.calls[0][1]
    assert PCARD.chunk_id not in user and PCARD_QUOTE not in user  # it cannot be quoted or cited
    assert PCARD.chunk_id not in a.intents[0]["retrieved"] and all(c.chunk_id != PCARD.chunk_id for c in a.citations)
    assert a.status == "answered" and "PCard" not in a.text and "suspended" not in a.text


def test_a_pcard_rule_relabelled_as_the_travel_card_rule_is_rejected_by_the_unchanged_verifier():
    ctl, stub = controller(rho_script(relabel=True))
    a = ctl.run(Q_TRAVEL)
    assert PCARD.chunk_id not in stub.calls[0][1]
    assert a.status == "abstained" and a.abstention_reason == "ungrounded_output"
    assert any("quote not found" in p for p in a.verification_problems) and a.citations == [] and a.claims == []


def test_a_pcard_question_still_retrieves_and_answers_from_pcard_evidence():
    ctl, stub = controller(rho_script())
    a = ctl.run(Q_PCARD)
    assert a.intents[0]["top_chunk"] == PCARD.chunk_id and PCARD.chunk_id in stub.calls[0][1]
    assert a.status == "answered" and [c.chunk_id for c in a.citations] == [PCARD.chunk_id]
    assert "PCard is suspended" in a.text
    assert DEFN.chunk_id not in stub.calls[0][1]  # for this question the Travel Card definition is the other entity


def test_a_travel_card_question_gets_the_pool_chunk_that_names_it_beyond_the_definition():
    ctl, stub = controller(rho_script())
    a = ctl.run(Q_TRAVEL)
    assert a.intents[0]["retrieved"][:MIN_NAMING] == [DEFN.chunk_id, TADV.chunk_id]  # the two that name it lead
    assert a.intents[0]["retrieved"][MIN_NAMING:] == [n.chunk_id for n in NOTES[:RETRIEVE_K - MIN_NAMING]]  # then as ranked
    assert DEFN.chunk_id in stub.calls[0][1] and TADV.chunk_id in stub.calls[0][1]
    assert {c.chunk_id for c in a.citations} == {DEFN.chunk_id, TADV.chunk_id}
    r = ctl.aligned(ctl.stack, Q_TRAVEL)
    assert [e.rank for e in r.evidence] == list(range(1, RETRIEVE_K + 1))
    wide = {e.chunk.chunk_id: e for e in retrieve(ctl.stack, Q_TRAVEL, WIDE_K).evidence}
    assert all(e.rerank_score == wide[e.chunk.chunk_id].rerank_score for e in r.evidence)  # scores are the reranker's


def test_contested_but_not_scarce_only_removes_the_other_entity():
    second = mk("pen", "Travel Card suspension rule: the travel card is suspended after sixty days.", org=RHO, doc="doc-rho")
    st = stack(PCARD, DEFN, second, *NOTES, TADV)
    ctl = MultiIntentController(st, StubProvider(rho_script()), ALIASES)
    raw, r = retrieve(st, Q_TRAVEL), ctl.aligned(st, Q_TRAVEL)
    assert PCARD.chunk_id in ids(raw.evidence)
    assert r.evidence == [e for e in raw.evidence if e.chunk.chunk_id != PCARD.chunk_id]  # order, ranks, scores kept
    assert TADV.chunk_id not in ids(r.evidence)  # no second retrieval: two shown chunks already name the entity


@pytest.mark.parametrize("q", [
    "What is the Rho University suspension rule?",  # names no entity
    "what is the rho university travel card suspension rule?",  # not written as a name
])
def test_a_question_that_names_no_entity_keeps_the_frozen_retrieval(q):
    ctl, _ = controller(rho_script())
    assert ctl.aligned(ctl.stack, q).evidence == retrieve(ctl.stack, q).evidence


def test_an_uncontested_retrieval_is_returned_as_it_is():
    st = stack(DEFN, *NOTES, TADV)  # nothing names another card
    ctl = MultiIntentController(st, StubProvider(rho_script()), ALIASES)
    seen = []
    ctl.retrieve = lambda s, q, k=RETRIEVE_K: seen.append(k) or retrieve(s, q, k)
    r = ctl.aligned(st, Q_TRAVEL)
    assert seen == [RETRIEVE_K] and r.evidence == retrieve(st, Q_TRAVEL).evidence
    assert TADV.chunk_id not in ids(r.evidence)  # scarce or not, an uncontested retrieval is never widened


# ---------------------------------------------------------------- the pure steps


def test_keep_scarce_and_align():
    other_org = mk("o", "The Travel Card is suspended after ninety days.", org="Sigma University", doc="doc-sigma")
    evidence = ev(PCARD, NOTES[0], other_org, DEFN, NOTES[1], TADV)
    assert contested(evidence, [TRAVEL]) and not contested(evidence, [PCARD_E, TRAVEL])  # each chunk names an asked one
    kept = keep(evidence, [TRAVEL])
    assert ids(kept) == ids(ev(NOTES[0], other_org, DEFN, NOTES[1], TADV)) and [e.rank for e in kept] == [2, 3, 4, 5, 6]
    assert scarce(kept[:3], [TRAVEL], [RHO]) and not scarce(kept, [TRAVEL], [RHO])  # another organization's does not count
    assert not scarce(kept[:3], [TRAVEL])  # a question that names no organization counts every chunk that names it
    aligned = align(evidence, [TRAVEL], [RHO])
    assert ids(aligned) == ids(ev(DEFN, TADV, NOTES[0], other_org, NOTES[1]))
    assert [e.rank for e in aligned] == [1, 2, 3, 4, 5] and C.MAX_SOURCES >= MIN_NAMING


# ---------------------------------------------------------------- with the multi-intent path of fix 1

A_ADV = mk("adv", "Alpha travel advance requirements: request an advance before departure.", org="Alpha University",
           doc="doc-alpha")
G_NON = mk("non", "Gamma non-travel advance policy: advances need approval by a dean.", org="Gamma University",
           doc="doc-gamma")
Q_LIST = ("What are Alpha University's travel advance requirements, Rho University's Travel Card rules, and Gamma "
          "University's non-travel advance policy?")


def list_script(system, user):
    claims = [(text, [label_of(user, chunk)], [quote]) for chunk, text, quote in [
        (A_ADV, "Alpha requires requesting an advance before departure", "request an advance before departure"),
        (PCARD, "The PCard is suspended for excessive personal purchases", PCARD_QUOTE),
        (DEFN, "A Travel Card is an employee travel card for university business travel",
         "an employee travel card for university business travel"),
        (G_NON, "Gamma non-travel advances need approval by a dean", "advances need approval by a dean"),
    ] if label_of(user, chunk)]
    return answered(*claims) if claims else insufficient_out()


def test_the_three_intent_question_keeps_its_intents_organizations_and_the_asked_entity():
    ctl, stub = controller(list_script, (PCARD, DEFN, *NOTES[:4], TADV, A_ADV, G_NON))
    a = ctl.run(Q_LIST)
    orgs = ["Alpha University", RHO, "Gamma University"]
    assert a.decomposition["multi"] and [i["organizations"] for i in a.intents] == [[o] for o in orgs]
    assert [i["topic"] for i in a.intents] == ["travel advance requirements", "Travel Card rules",
                                                "non-travel advance policy"]
    by_id = {c.chunk_id: c for c in ctl.stack.chunks}
    for rec, org in zip(a.intents, orgs):  # organization-isolated evidence, as before
        assert rec["retrieved"] and {by_id[cid].organization for cid in rec["retrieved"]} == {org}
    rho = a.intents[1]
    assert PCARD.chunk_id in ids(retrieve(ctl.stack, rho["sub_query"]).evidence)  # the retrieval itself returns it
    assert PCARD.chunk_id not in rho["retrieved"] and {DEFN.chunk_id, TADV.chunk_id} <= set(rho["retrieved"])
    assert rho["top_chunk"] == DEFN.chunk_id
    # whichever path the coverage gate takes (the whole question's context, or the fused one), one call, no PCard
    assert a.strategy in (DELEGATE, FUSED) and len(stub.calls) == 1 and question_of(stub.calls[0][1]).endswith(Q_LIST)
    assert PCARD.chunk_id not in stub.calls[0][1]
    assert all(c.chunk_id in stub.calls[0][1] for c in (A_ADV, DEFN, G_NON))
    assert a.status == "answered" and [cl["intents"] for cl in a.claims] == [[0], [1], [2]]


@pytest.mark.parametrize("q, topics", [
    ("At Alpha University, what are the travel advance requirements, and at Rho University, what are the Travel Card "
     "rules?", ["travel advance requirements", "Travel Card rules"]),
    ("For Alpha University, what are the travel advance requirements; for Rho University, what are the Travel Card "
     "rules?", ["travel advance requirements", "Travel Card rules"]),
])
def test_institution_led_clauses_keep_organization_isolation_and_the_asked_entity(q, topics):
    ctl, stub = controller(list_script, (PCARD, DEFN, *NOTES[:4], TADV, A_ADV, G_NON))
    a = ctl.run(q)
    assert a.decomposition["multi"] and [i["organizations"] for i in a.intents] == [["Alpha University"], [RHO]]
    assert [i["topic"] for i in a.intents] == topics
    by_id = {c.chunk_id: c for c in ctl.stack.chunks}
    for rec, org in zip(a.intents, ["Alpha University", RHO]):
        assert rec["retrieved"] and {by_id[cid].organization for cid in rec["retrieved"]} == {org}
    rho = a.intents[1]
    assert PCARD.chunk_id in ids(retrieve(ctl.stack, rho["sub_query"]).evidence)  # the retrieval itself returns it
    assert PCARD.chunk_id not in rho["retrieved"] and rho["top_chunk"] == DEFN.chunk_id
    assert len(stub.calls) == 1 and PCARD.chunk_id not in stub.calls[0][1]
    assert a.status == "answered" and "PCard" not in a.text
    assert {c.chunk_id for c in a.citations} <= {A_ADV.chunk_id, DEFN.chunk_id, TADV.chunk_id}


def test_an_institution_led_pcard_clause_still_gets_pcard_evidence():
    ctl, stub = controller(list_script, (PCARD, DEFN, *NOTES[:4], TADV, A_ADV, G_NON))
    a = ctl.run("At Alpha University, what are the travel advance requirements, and at Rho University, what is the PCard "
                "suspension rule?")
    rho = a.intents[1]
    assert rho["organizations"] == [RHO] and rho["top_chunk"] == PCARD.chunk_id and DEFN.chunk_id not in rho["retrieved"]
    assert PCARD.chunk_id in stub.calls[0][1] and PCARD.chunk_id in [c.chunk_id for c in a.citations]


def test_intents_that_name_no_organization_and_no_entity_keep_their_whole_retrieval():
    from adaptive.multi.decompose import decompose
    from adaptive.multi.fusion import retrieve_intent
    from tests.test_multi_intent_controller import DEADLINE, FILLERS, PENALTY, Q_TWO

    st = stack(DEADLINE, PENALTY, *FILLERS, PCARD)
    ctl = MultiIntentController(st, StubProvider(list_script), ALIASES)
    for intent in decompose(Q_TWO, ctl.organizations, ALIASES).intents:
        assert intent.organizations == [] and entities(intent.sub_query, ctl.organizations, ALIASES) == []
        assert retrieve_intent(st, intent, ctl.aligned).retrieval.evidence == retrieve(st, intent.sub_query).evidence
