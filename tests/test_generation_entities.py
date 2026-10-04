"""Entity alignment in the verifier (offline): a claim that names an entity is not grounded by evidence about another
entity of the same kind, however alike the wording. Deterministic rules over ENTITY_GROUPS; no network, no models.
"""

import pytest

from generation.answer import GroundedAnswerer
from generation.context import assemble
from generation.providers import StubProvider
from generation.validate import ENTITY_GROUPS, entities_named, normalize
from tests.test_generation import CARD, answered, ev, mk
from tests.test_generation_verification import FEB_CARD, check

RU = dict(org="Rutgers University", doc="ru", title="Travel and Expense Management")
# the passages of the historical failure, as the corpus has them
PCARD = mk("pcard", "The purchase of personal items on a university purchasing card (PCard) is strictly prohibited. "
           "Intentional or excessive accidental purchases of personal items may result in the suspension and/or "
           "revocation of the employee’s PCard.", section=("Credit Card Restrictions",), **RU)
PCARD_QUOTE = "may result in the suspension and/or revocation of the employee’s PCard"
BOTH = mk("both", "A travel advance is provided when the use of the departmental purchasing card (PCard) or employee "
          "travel card (TCard) may not be accepted. Cash advances cannot be used to cover the cost of items that can be "
          "purchased with a PCard or TCard. PCard statements are reconciled monthly.", section=("Travel Advances",), **RU)
TRAVEL = mk("tc", "University-Issued Travel Card charges must be submitted within 30 days. If they are not, the card "
            "will be suspended.", section=("University Travel Card Penalties",), org="University of Connecticut", doc="uc")
PENALTY = mk("pen", "If charges are not submitted within 60 days, the card will be suspended.",
             section=("University Travel Card Penalties",), org="University of Connecticut", doc="uc")
PLAIN = mk("plain", "If charges are not submitted within 60 days, the card will be suspended.", doc="plain")
PROCUREMENT = mk("proc", "The Procurement Card may be suspended after repeated misuse.", org="Stanford University", doc="st")

TRAVEL_CLAIM = "Rutgers may suspend or revoke a travel card for excessive personal purchases"


def problems(source_chunks, text, quotes, sources=("S1",)):
    return check(assemble(ev(*source_chunks)), (text, list(sources), list(quotes))).problems


# ---------------------------------------------------------------- aliases


@pytest.mark.parametrize("text, expected", [
    ("the Travel Card", {"Travel Card"}), ("University Travel Cards", {"Travel Card"}),
    ("a University-Issued Travel Card", {"Travel Card"}), ("University Issued Travel Card", {"Travel Card"}),
    ("an employee travel card (TCard)", {"Travel Card"}), ("a T‑Card", {"Travel Card"}),  # a non-breaking hyphen
    ("the PCard", {"PCard"}), ("P-Cards", {"PCard"}), ("a university purchasing card", {"PCard"}),
    ("the Procurement Card", {"PCard"}), ("a PCard or TCard", {"PCard", "Travel Card"}),
    ("the card", set()), ("a personal credit card", set()), ("a postcard about travel", set()), ("cardholder", set()),
])
def test_aliases_name_one_entity_each(text, expected):
    assert entities_named(normalize(text)) == {"card": expected}


def test_no_alias_belongs_to_two_entities_of_a_group():
    samples = ["travel card", "t-card", "tcard", "p-card", "pcard", "purchasing card", "procurement card",
               "university-issued travel card"]
    for members in ENTITY_GROUPS.values():
        for s in samples:
            assert sum(bool(alias.search(s)) for alias in members.values()) == 1


# ---------------------------------------------------------------- A - F: the rule


def test_a_travel_card_claim_on_pcard_evidence_is_rejected():
    found = problems([PCARD], TRAVEL_CLAIM, [PCARD_QUOTE])
    assert found == ["claim 1: names the Travel Card, but its supporting quote(s) name only the PCard: evidence about a "
                     "different card does not support it"]
    # also when the quote itself names no card: the source it comes from is about the PCard only
    found = problems([PCARD], TRAVEL_CLAIM, ["Intentional or excessive accidental purchases of personal items"])
    assert found == ["claim 1: names the Travel Card, but its cited source(s) ['S1'] name only the PCard: evidence about a "
                     "different card does not support it"]


def test_a_pcard_claim_on_pcard_evidence_passes():
    v = check(assemble(ev(PCARD)), ("Rutgers may suspend or revoke an employee's PCard for excessive personal purchases",
                                    ["S1"], [PCARD_QUOTE]))
    assert v.ok and v.problems == []


def test_a_travel_card_claim_on_travel_card_evidence_passes():
    assert problems([TRAVEL], "Travel Card charges must be submitted within 30 days",
                    ["Travel Card charges must be submitted within 30 days"]) == []
    assert problems([CARD], "Travel Card charges must be submitted within 30 days of the trip end date",
                    ["Travel Card charges must be submitted within 30 days"]) == []


@pytest.mark.parametrize("claim, chunk, quote", [
    # D: University Travel Card = University-Issued Travel Card = TCard
    ("University Travel Card charges must be submitted within 30 days", TRAVEL, "charges must be submitted within 30 days"),
    ("A TCard is suspended when charges are not submitted", TRAVEL, "the card will be suspended"),
    ("Items that can be bought with a Travel Card are not covered by cash advances", BOTH,
     "items that can be purchased with a PCard or TCard"),
    # E: PCard = Purchasing Card = Procurement Card
    ("A purchasing card may be suspended for excessive personal purchases", PCARD, PCARD_QUOTE),
    ("The PCard may be suspended after repeated misuse", PROCUREMENT, "The Procurement Card may be suspended after repeated misuse"),
    ("Personal items may not be bought on a Procurement Card", PCARD, "university purchasing card (PCard) is strictly prohibited"),
])
def test_aliases_of_the_claimed_entity_are_accepted(claim, chunk, quote):
    assert problems([chunk], claim, [quote]) == []


def test_evidence_naming_both_entities_supports_the_one_it_names_where_the_claim_is_supported():
    # F: the quote names the claimed entity (and the other one too)
    assert problems([BOTH], "Cash advances cannot pay for items that can be bought with a TCard",
                    ["items that can be purchased with a PCard or TCard"]) == []
    # the quote names no card; the source names the claimed entity: not rejected because the PCard is there as well
    assert problems([BOTH], "A travel advance is for when the Travel Card may not be accepted",
                    ["A travel advance is provided when the use of the departmental"]) == []
    # but a sentence about the PCard only does not support a Travel Card claim, even from a source that names both
    assert problems([BOTH], "Travel Card statements are reconciled monthly", ["PCard statements are reconciled monthly"]) == [
        "claim 1: names the Travel Card, but its supporting quote(s) name only the PCard: evidence about a different card "
        "does not support it"]
    # a claim about both needs both named where it is supported
    assert problems([BOTH], "Neither a PCard nor a Travel Card purchase is covered", ["purchased with a PCard or TCard"]) == []
    assert len(problems([BOTH], "PCard and Travel Card statements are reconciled monthly",
                        ["PCard statements are reconciled monthly"])) == 1


def test_the_source_heading_counts_as_naming_the_entity():
    assert problems([PENALTY], "The University Travel Card is suspended after 60 days", ["the card will be suspended"]) == []


def test_a_claim_citing_two_sources_is_judged_where_each_quote_was_found():
    two = [PCARD, TRAVEL]
    assert problems(two, "A Travel Card is suspended when charges are late", ["the card will be suspended"], ["S1", "S2"]) == []
    found = problems(two, TRAVEL_CLAIM, [PCARD_QUOTE], ["S1", "S2"])  # citing S2 as well does not launder the quote
    assert len(found) == 1 and "supporting quote(s) name only the PCard" in found[0]


# ---------------------------------------------------------------- H: everything else is unchanged


@pytest.mark.parametrize("claim, chunk, quote", [
    ("The card is suspended after 60 days", PLAIN, "the card will be suspended"),  # the claim names no entity
    ("The Travel Card is suspended after 60 days", PLAIN, "the card will be suspended"),  # the evidence names none
    ("The PCard is suspended after 60 days", PLAIN, "the card will be suspended"),
    ("If card charges are not submitted within 60 days the card is suspended", FEB_CARD, "the card will be suspended"),
    ("Suspension and revocation follow excessive personal purchases", PCARD, PCARD_QUOTE),  # no entity in the claim
])
def test_claims_or_evidence_without_these_entities_are_not_judged_by_the_rule(claim, chunk, quote):
    assert not [p for p in problems([chunk], claim, [quote]) if "different card" in p]


def test_the_other_checks_still_apply_to_a_claim_that_names_an_entity():
    found = problems([TRAVEL], "Travel Card charges must be submitted within 45 days", ["charges must be submitted within 30 days"])
    assert found == ["claim 1: number(s) ['45'] not in cited source(s)"]
    found = problems([PCARD], TRAVEL_CLAIM, ["the Travel Card may be revoked"])
    assert len(found) == 1 and "quote not found" in found[0]  # no quote was found: nothing to judge the entity on


# ---------------------------------------------------------------- G: the historical case, end to end


def answer(script, question, *chunks):
    return GroundedAnswerer(StubProvider(script)).answer(question, ev(*chunks))


def test_a_pcard_rule_stated_as_the_travel_card_rule_is_never_a_grounded_answer():
    question = "What is the Rutgers Travel Card suspension rule?"
    relabelled = lambda s, u: answered((TRAVEL_CLAIM, ["S1"], [PCARD_QUOTE]))  # noqa: E731
    for chunks in ([PCARD], [PCARD, BOTH], [BOTH, PCARD]):
        cite = "S1" if chunks[0] is PCARD else "S2"
        a = answer(lambda s, u, cite=cite: answered((TRAVEL_CLAIM, [cite], [PCARD_QUOTE])), question, *chunks)
        assert (a.status, a.abstention_reason) == ("abstained", "ungrounded_output")
        assert a.citations == [] and a.claims == [] and "travel card" not in a.text.lower()
        assert a.verification_problems == [
            "claim 1: names the Travel Card, but its supporting quote(s) name only the PCard: evidence about a different "
            "card does not support it"]
    assert answer(relabelled, question, PCARD).raw_output  # the rejected output is kept for inspection, not shown


def test_the_same_passage_grounds_a_claim_about_the_pcard_and_an_insufficient_answer_stays_an_abstention():
    honest = lambda s, u: answered(("The employee's PCard may be suspended or revoked for excessive personal purchases",  # noqa: E731
                                    ["S1"], [PCARD_QUOTE]))
    a = answer(honest, "What is the Rutgers PCard suspension rule?", PCARD)
    assert a.status == "answered" and [c.chunk_id for c in a.citations] == [PCARD.chunk_id] and "PCard" in a.text
    none = lambda s, u: {"status": "insufficient_evidence", "claims": [], "not_in_sources": [], "abstention_reason": "x"}  # noqa: E731
    a = answer(none, "What is the Rutgers Travel Card suspension rule?", PCARD)
    assert (a.status, a.abstention_reason) == ("abstained", "model_insufficient_evidence")
