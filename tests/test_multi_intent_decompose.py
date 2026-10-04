"""Multi-intent decomposition (offline, deterministic): what is split, what is not, sub-queries, organizations."""

import pytest

from adaptive.multi.decompose import MAX_INTENTS, decompose, load_aliases
from temporal.intent import parse_query

ALIASES = load_aliases()
ORGS = set(ALIASES.values()) | {"University of Michigan", "University of Rochester", "McGill University"}
UCONN = "University of Connecticut"


def d(q):
    return decompose(q, ORGS, ALIASES)


# ---------------------------------------------------------------- not split


@pytest.mark.parametrize("q", [
    "How often must UT Austin user accounts be reviewed?",
    # a trailing non-question sentence belongs to the question
    "At Stanford, can a $240 invoice overage on a $2,000 standard purchase order be paid without a written change "
    "order? Explain why.",
    # no question sentence
    "Compare the February 1, 2026 and July 1, 2026 UConn Travel Card penalty rules.",
    # temporal compare: versions are phase 1's concern
    "What did UConn's travel procedures effective February 1, 2026 and those effective July 1, 2026 each say about "
    "when a University Travel Card is suspended?",
    # "and" between noun phrases / amounts
    "Is there a limit on the tips and gratuities Rutgers will reimburse?",
    "Are Penn purchases of $50,000 or more subject to review and approval before issuance?",
    # an auxiliary after "and" needs a comma
    "What happens to charges that are submitted and are approved after the deadline?",
    # too short to be a question of its own
    "What must a UConn traveler submit, and when?",
    "What must a UConn traveler submit? Why?",
    # refers back to the previous clause: not independent
    "Which clause of UT Austin's information resources policy sets how often user accounts must be reviewed, and how "
    "often is that?",
    "Oregon State expects a contract price of $180,000. Which general procurement method applies, and what does that "
    "method involve?",
    "What is Rutgers' tip limit? When does it apply?",
])
def test_single_intent_questions_are_not_split(q):
    r = d(q)
    assert not r.multi and len(r.intents) == 1
    assert r.intents[0].sub_query == q  # the question itself, untouched


def test_compare_reason_is_recorded():
    r = d("Compare the multi-bedroom accommodation rules in UConn's procedures effective February 1, 2026 and July 1, 2026?")
    assert not r.multi and "compare" in r.reason


# ---------------------------------------------------------------- split


def test_the_phase_2_example_is_split_and_the_organization_carried():
    r = d("What is the UConn University Travel Card submission deadline, and what happens if charges aren't submitted "
          "on time?")
    assert r.multi and [i.text for i in r.intents] == [
        "What is the UConn University Travel Card submission deadline?",
        "what happens if charges aren't submitted on time?"]
    assert r.intents[0].organizations == [UCONN] and r.intents[0].carried == []
    assert r.intents[1].organizations == [] and r.intents[1].carried == [UCONN]
    assert r.intents[1].sub_query == f"what happens if charges aren't submitted on time? ({UCONN})"


def test_leading_scope_applies_to_every_clause_and_keeps_the_version():
    r = d("Under UConn's travel procedures effective July 1, 2026, within how many days must University Travel Card "
          "charges be submitted, and when is the card suspended?")
    assert r.multi and len(r.intents) == 2
    scope = "Under UConn's travel procedures effective July 1, 2026, "
    assert all(i.sub_query.startswith(scope) for i in r.intents)
    kinds = [parse_query(i.sub_query) for i in r.intents]
    assert [k.kind for k in kinds] == ["point_in_time", "point_in_time"] and kinds[0].dates == kinds[1].dates
    assert r.intents[1].carried == []  # the scope names UConn


def test_context_sentence_is_shared():
    ctx = "A University of Rochester researcher plans travel to a high-risk location."
    r = d(f"{ctx} How far ahead should the travel request be submitted, and who must approve the trip?")
    assert r.multi and all(i.sub_query.startswith(ctx + " ") for i in r.intents)


def test_two_question_sentences():
    r = d("What airfare does the University of Michigan pay for? How far in advance should a University of Michigan "
          "traveler book airfare?")
    assert r.multi and len(r.intents) == 2 and r.intents[1].text.startswith("How far in advance")


def test_serial_list_of_three_questions_across_organizations():
    r = d("How often must UT Austin user accounts be reviewed, what mileage rate does the University of Michigan "
          "reimburse, and how far in advance must a Rutgers travel advance request be submitted?")
    assert r.multi and len(r.intents) == 3
    assert [i.organizations for i in r.intents] == [["The University of Texas at Austin"], ["University of Michigan"],
                                                    ["Rutgers University"]]
    assert all(i.carried == [] for i in r.intents)


def test_an_auxiliary_clause_after_comma_and():
    r = d("What did UConn's February 2026 procedures say about when a University Travel Card is suspended, and may a "
          "traveler currently pay for a multi-bedroom accommodation with a University Travel Card?")
    assert r.multi and r.intents[1].text.startswith("may a traveler currently")
    assert parse_query(r.intents[0].sub_query).kind == "point_in_time"
    assert parse_query(r.intents[1].sub_query).kind == "current"  # each intent keeps its own temporal words


def test_more_than_max_intents_goes_to_phase_1():
    q = ("What does Rutgers reimburse for tips, what does Stanford require for invoices, how often must UT Austin "
         "accounts be reviewed, and what airfare does the University of Michigan pay for?")
    r = d(q)
    assert MAX_INTENTS == 3 and not r.multi and r.flags == ["too_many_intents"] and r.intents[0].sub_query == q


def test_ambiguous_organization_is_not_carried():
    r = d("What does Rutgers reimburse for tips, what does Stanford require for invoices, and how are travel advances "
          "requested?")
    assert r.multi and r.intents[2].carried == [] and r.flags == ["ambiguous_organization:intent_2"]


def test_an_organization_outside_the_alias_list_is_not_overwritten():
    r = d("How far ahead should a University of Rochester traveler submit a request for travel to a high-risk location, "
          "and what purchase amount requires competitive bids at Harvard University?")
    assert r.multi and r.intents[1].organizations == [] and r.intents[1].carried == []


def test_deterministic():
    q = "At Penn, what happens to purchase orders of $50,000 or more before issuance, and when are competitive bids not required?"
    assert d(q).to_dict() == d(q).to_dict()
    r = d(q)
    assert r.multi and all(i.sub_query.startswith("At Penn, ") for i in r.intents)


# ---------------------------------------------------------------- one question about a list of organizations' subjects

RUTGERS, MCGILL = "Rutgers University", "McGill University"
Q_LIST_3 = ("What are UConn's travel advance requirements, Rutgers University's Travel Card rules, and McGill "
            "University's non-travel advance policy?")
Q_LIST_2 = "What are UConn's travel advance requirements, and what about Rutgers Travel Card rules?"


def test_three_organizations_in_one_list_question_are_three_intents():
    r = d(Q_LIST_3)
    assert r.multi and len(r.intents) == 3 and "list of organizations' subjects" in r.reason
    assert [i.text for i in r.intents] == ["What are UConn's travel advance requirements?",
                                           "What are Rutgers University's Travel Card rules?",
                                           "What are McGill University's non-travel advance policy?"]
    assert [i.sub_query for i in r.intents] == [i.text for i in r.intents]  # retrieval sees one intent's words only
    assert r.flags == [] and all(i.carried == [] and i.outside_corpus == [] for i in r.intents)


def test_two_organizations_in_two_question_clauses_are_two_intents():
    r = d(Q_LIST_2)
    assert r.multi and [i.text for i in r.intents] == ["What are UConn's travel advance requirements?",
                                                       "what about Rutgers Travel Card rules?"]


@pytest.mark.parametrize("q, organization, topic", [
    ("What are the UConn travel advance requirements?", UCONN, "travel advance requirements"),
    ("What are Rutgers University's Travel Card rules?", RUTGERS, "Travel Card rules"),
    ("What is McGill University's non-travel advance policy?", MCGILL, "non-travel advance policy"),
])
def test_one_organization_and_one_subject_is_one_intent(q, organization, topic):
    r = d(q)
    assert not r.multi and len(r.intents) == 1 and r.intents[0].sub_query == q
    assert (r.intents[0].organizations, r.intents[0].topic) == ([organization], topic)


@pytest.mark.parametrize("q, expected", [
    (Q_LIST_3, [(UCONN, "travel advance requirements"), (RUTGERS, "Travel Card rules"),
                (MCGILL, "non-travel advance policy")]),
    (Q_LIST_2, [(UCONN, "travel advance requirements"), (RUTGERS, "Travel Card rules")]),
    ("What is UConn's travel advance limit and Rutgers' tip limit?", [(UCONN, "travel advance limit"),
                                                                       (RUTGERS, "tip limit")]),
])
def test_each_intent_keeps_its_own_organization_and_topic(q, expected):
    r = d(q)
    assert [(i.organizations, i.topic) for i in r.intents] == [([org], topic) for org, topic in expected]
    for i in r.intents:  # no intent's query carries another intent's organization or subject
        others = [x for x in r.intents if x is not i]
        assert all(o.topic not in i.sub_query for o in others)
        alone = d(i.sub_query)  # asked by itself, the intent is one question about the same organization
        assert not alone.multi and alone.intents[0].organizations == i.organizations


def test_a_list_item_keeps_its_own_temporal_words_and_the_shared_scope():
    r = d("Under the procedures effective July 1, 2026, what are UConn's travel advance requirements and Rutgers' "
          "current Travel Card rules?")
    assert r.multi and len(r.intents) == 2
    assert all(i.sub_query.startswith("Under the procedures effective July 1, 2026, ") for i in r.intents)
    assert [i.topic for i in r.intents] == ["travel advance requirements", "current Travel Card rules"]


def test_a_list_item_naming_an_institution_outside_the_corpus_is_its_own_unsupported_intent():
    r = d("What are UConn's travel advance requirements and Harvard University's bid rules?")
    assert r.multi and r.intents[1].organizations == [] and r.intents[1].outside_corpus == ["Harvard University"]
    assert r.intents[1].topic == "bid rules" and r.intents[0].organizations == [UCONN]


@pytest.mark.parametrize("q", [
    # "and" / commas inside one organization's subject
    "What are Rutgers' travel advance requirements and settlement rules?",
    "What are UConn's travel advance requirements, limits, and deadlines?",
    # no item of its own for the first organization: one comparison, not two needs
    "What are UConn's and Rutgers' Travel Card rules?",
    "What are the differences between UConn's travel rules and Rutgers' travel rules?",
    # the same organization twice
    "What are UConn's travel advance requirements and UConn's Travel Card rules?",
    # not a "What is / are <list>" question: the items are not what is asked for
    "Does UConn's travel policy or Rutgers' travel policy cover tips?",
    "How do UConn's travel advance requirements and Rutgers' Travel Card rules treat late reports?",
])
def test_a_list_that_is_not_one_subject_per_organization_is_not_split(q):
    r = d(q)
    assert not r.multi and r.intents[0].sub_query == q


def test_more_than_max_intents_in_a_list_goes_to_phase_1():
    q = ("What are UConn's advance rules, Rutgers' card rules, McGill University's advance policy, and Stanford's "
         "invoice rules?")
    r = d(q)
    assert not r.multi and r.flags == ["too_many_intents"] and r.intents[0].sub_query == q


def test_topic_is_recorded_only_for_a_question_that_names_what_it_asks_for():
    assert d("How often must UT Austin user accounts be reviewed?").intents[0].topic == ""
    assert d("At UConn, what is the University Travel Card suspension rule?").intents[0].topic == (
        "University Travel Card suspension rule")
    r = d("What is the UConn University Travel Card submission deadline, and what happens if charges aren't submitted "
          "on time?")
    assert [i.topic for i in r.intents] == ["University Travel Card submission deadline", ""]


# ---------------------------------------------------------------- institution-led clauses and institution lists

Q_LED = "At UConn, what are the travel reimbursement rules, and at Rutgers, what are the Travel Card rules?"


def parts(r):
    return [(i.sub_query, i.organizations, i.topic) for i in r.intents]


def test_institution_led_clauses_are_separate_intents_each_with_its_own_scope():
    r = d(Q_LED)
    assert r.multi and parts(r) == [
        ("At UConn, what are the travel reimbursement rules?", [UCONN], "travel reimbursement rules"),
        ("at Rutgers, what are the Travel Card rules?", [RUTGERS], "Travel Card rules")]
    assert [i.text for i in r.intents] == ["what are the travel reimbursement rules?", "what are the Travel Card rules?"]
    assert all(i.carried == [] and i.outside_corpus == [] for i in r.intents) and r.flags == []
    assert "UConn" not in r.intents[1].sub_query and "Rutgers" not in r.intents[0].sub_query  # no scope is shared


@pytest.mark.parametrize("q, expected", [
    ("For UConn, what are the travel advance requirements, and for Rutgers, what are the PCard rules?",
     [("For UConn, what are the travel advance requirements?", [UCONN], "travel advance requirements"),
      ("for Rutgers, what are the PCard rules?", [RUTGERS], "PCard rules")]),
    ("At UConn, what is the travel advance limit; at McGill, what is the non-travel advance policy?",
     [("At UConn, what is the travel advance limit?", [UCONN], "travel advance limit"),
      ("at McGill, what is the non-travel advance policy?", [MCGILL], "non-travel advance policy")]),
    ("At UConn, what are the travel advance rules, at Rutgers, what are the Travel Card rules, and at McGill, what is "
     "the non-travel advance policy?",
     [("At UConn, what are the travel advance rules?", [UCONN], "travel advance rules"),
      ("at Rutgers, what are the Travel Card rules?", [RUTGERS], "Travel Card rules"),
      ("at McGill, what is the non-travel advance policy?", [MCGILL], "non-travel advance policy")]),
    ("At UConn, is a Travel Card required, and at Rutgers University, is a PCard allowed for airfare?",
     [("At UConn, is a Travel Card required?", [UCONN], ""),
      ("at Rutgers University, is a PCard allowed for airfare?", [RUTGERS], "")]),
])
def test_institution_led_forms(q, expected):
    r = d(q)
    assert r.multi and parts(r) == expected


@pytest.mark.parametrize("q, expected", [
    ("What are the Travel Card rules at UConn and Rutgers?",
     [("What are the Travel Card rules at UConn?", [UCONN], "Travel Card rules"),
      ("What are the Travel Card rules at Rutgers?", [RUTGERS], "Travel Card rules")]),
    ("At UConn, Rutgers and McGill, what are the travel advance rules?",
     [("At UConn, what are the travel advance rules?", [UCONN], "travel advance rules"),
      ("At Rutgers, what are the travel advance rules?", [RUTGERS], "travel advance rules"),
      ("At McGill, what are the travel advance rules?", [MCGILL], "travel advance rules")]),
    ("For UConn and for Rutgers, what is the tip limit?",
     [("For UConn, what is the tip limit?", [UCONN], "tip limit"),
      ("For Rutgers, what is the tip limit?", [RUTGERS], "tip limit")]),
    ("How are travel advances requested at Rutgers University, at McGill University, or at UConn?",
     [("How are travel advances requested at Rutgers University?", [RUTGERS], ""),
      ("How are travel advances requested at McGill University?", [MCGILL], ""),
      ("How are travel advances requested at UConn?", [UCONN], "")]),
])
def test_one_question_asked_of_a_list_of_institutions_is_asked_once_per_institution(q, expected):
    r = d(q)
    assert r.multi and parts(r) == expected and "list of institutions" in r.reason


@pytest.mark.parametrize("text, organizations", [
    ("What is McGill's non-travel advance policy?", [MCGILL]),
    ("at McGill, what is the non-travel advance policy?", [MCGILL]),
    ("What does McGill University reimburse?", [MCGILL]),
    ("What are UConn's travel advance requirements?", [UCONN]),
    ("What are Rutgers' Travel Card rules?", [RUTGERS]),
    ("What are the rules at Rutgers University and McGill?", [RUTGERS, MCGILL]),
    ("What is the McGillivray policy?", []),  # an alias is a whole word
])
def test_short_names_of_institutions_are_recognized(text, organizations):
    from adaptive.multi.decompose import mentions

    assert ALIASES["McGill"] == MCGILL and ALIASES["UConn"] == UCONN and ALIASES["Rutgers"] == RUTGERS
    assert mentions(text, ORGS, ALIASES) == organizations


def test_a_short_name_makes_a_list_item_its_own_intent():
    r = d("What are McGill's non-travel advance rules and Rutgers' Travel Card rules?")
    assert r.multi and [(i.organizations, i.topic) for i in r.intents] == [([MCGILL], "non-travel advance rules"),
                                                                            ([RUTGERS], "Travel Card rules")]


def test_a_shared_scope_without_an_institution_still_applies_to_an_institution_led_clause():
    r = d("Under the procedures effective July 1, 2026, what is UConn's card deadline, and at Rutgers, what is the tip limit?")
    assert r.multi and [i.sub_query for i in r.intents] == [
        "Under the procedures effective July 1, 2026, what is UConn's card deadline?",
        "Under the procedures effective July 1, 2026, at Rutgers, what is the tip limit?"]
    assert [parse_query(i.sub_query).kind for i in r.intents] == ["point_in_time", "point_in_time"]


def test_an_institution_outside_the_corpus_leads_its_own_unsupported_intent():
    r = d("At Harvard University, what is the procurement policy, and at Penn, what is the procurement policy?")
    assert r.multi and r.intents[0].outside_corpus == ["Harvard University"] and r.intents[0].organizations == []
    assert r.intents[1].organizations == ["University of Pennsylvania"] and r.intents[1].outside_corpus == []
    assert r.intents[0].carried == []  # Penn's evidence never stands in for it


@pytest.mark.parametrize("q", [
    # one institution: nothing to separate
    "At UConn, what are the travel advance requirements?",
    "For Rutgers, what are the Travel Card rules?",
    "What are the Travel Card rules at UConn?",
    "At UConn, what is the deadline for University Travel Card charges and receipts?",
    "What are the travel advance rules at UConn and at UConn's regional campuses?",
    "What is the travel advance rule at UConn for domestic and international trips?",
    # a scope that names no institution does not start a clause of its own
    "At UConn, what is the deadline, and in that case, what happens to the card?",
    "At UConn, what is the deadline, and for late charges, what is the penalty?",
    # a comparison is one need
    "What is the difference between the Travel Card rules at UConn and Rutgers?",
    "Are the Travel Card rules the same at UConn and Rutgers?",
    "Do both UConn and Rutgers require receipts?",
    # the list is not the end of the question, or not a list of institutions only
    "What do travelers at UConn and Rutgers submit after a trip?",
    "What are the rules for travel and lodging at Rutgers?",
])
def test_no_false_decomposition(q):
    r = d(q)
    assert not r.multi and r.intents[0].sub_query == q


def test_the_second_led_clause_of_an_earlier_single_intent_case_is_now_its_own_intent():
    from evaluation.streaming.cases import CASES as STREAMING_CASES

    for case in STREAMING_CASES:  # the phase 6 cases were chosen because this form was not split
        r = d(case["question"])
        assert r.multi and len(r.intents) == 2 and r.intents[1].sub_query.startswith("at ")


def test_more_than_max_intents_of_a_led_or_listed_question_goes_to_phase_1():
    for q in ("At UConn, what is A's limit, at Rutgers, what is B's limit, at McGill, what is C's limit, and at Stanford, "
              "what is D's limit?", "What are the Travel Card rules at UConn, Rutgers, McGill and Stanford?"):
        r = d(q)
        assert not r.multi and r.flags == ["too_many_intents"] and r.intents[0].sub_query == q


# ---------------------------------------------------------------- the existing integration questions


def test_integration_questions_decomposition():
    """Only two of the 27 frozen integration questions have two independent clauses; everything else is single."""
    from evaluation.integration.cases import CASES

    multi = {c["id"] for c in CASES if d(c["question"]).multi}
    assert multi == {"i-uc-jul-submit", "i-uc-personal-leg"}


def test_phase_2_cases_decompose_as_declared():
    from evaluation.multi_intent.cases import CASES as MI_CASES

    for c in MI_CASES:
        r = d(c["question"])
        assert r.multi == c["multi"], c["id"]
        if c["multi"]:
            assert len(r.intents) == len(c["intents"]), c["id"]


def test_phase_2_case_gold_resolves_to_one_chunk_each():
    from chunking.pipeline import load_chunks
    from evaluation.integration.cases import resolve
    from evaluation.multi_intent.cases import CASES as MI_CASES

    try:
        cbd = load_chunks()
    except FileNotFoundError:
        pytest.skip("chunks not generated")
    ids = [c["id"] for c in MI_CASES]
    assert len(ids) == len(set(ids))
    for c in MI_CASES:
        for p in c["intents"]:
            for unit in p["gold"]:
                assert unit and all(resolve(r, cbd) for r in unit), c["id"]  # raises unless exactly one chunk


# ---------------------------------------------------------------- aliases


def test_aliases_name_corpus_organizations_and_are_all_used():
    from chunking.pipeline import load_chunks
    from evaluation.integration.cases import CASES
    from evaluation.multi_intent.cases import CASES as MI_CASES

    try:
        chunks = [c for cs in load_chunks().values() for c in cs]
    except FileNotFoundError:
        pytest.skip("chunks not generated")
    if not chunks:
        pytest.skip("chunks not generated")
    corpus_orgs = {c.organization for c in chunks}
    assert set(ALIASES.values()) <= corpus_orgs
    from evaluation.retrieval.cases import CASES as RETRIEVAL_CASES

    questions = ([c["question"] for c in CASES] + [c["question"] for c in MI_CASES]
                 + [c["query"] for c in RETRIEVAL_CASES])  # "McGill" is a short form of the retrieval benchmark
    for alias in ALIASES:
        assert any(alias in q for q in questions), f"alias {alias!r} is not needed by any case"


# ---------------------------------------------------------------- organization boundary


@pytest.mark.parametrize("text, outside", [
    ("what purchase amount requires competitive bids at Harvard University?", ["Harvard University"]),
    ("Which University Travel Card rules apply at the University of Chicago?", ["University of Chicago"]),
    ("Does Boston College pay for tips?", ["Boston College"]),
    ("What is the UConn University Travel Card submission deadline?", []),  # "UConn University": an alias
    ("How often must The University of Texas at Austin review accounts?", []),
    ("What does the University of Rochester say about tips?", []),
    ("What is the threshold at Oregon State University?", []),
    ("Which University Travel Card charges are late?", []),  # "Which" is not a name
    ("Who approves travel for a Senior Institutional Official?", []),
])
def test_outside_corpus_names(text, outside):
    from adaptive.multi.decompose import outside_corpus

    assert outside_corpus(text, ORGS, ALIASES) == outside


def test_an_out_of_corpus_intent_is_recorded_and_gets_no_carried_organization():
    r = d("How far ahead should a University of Rochester traveler submit a request for travel to a high-risk location, "
          "and what purchase amount requires competitive bids at Harvard University?")
    assert r.multi and r.intents[1].outside_corpus == ["Harvard University"] and r.intents[1].carried == []
    assert r.intents[0].outside_corpus == []


def test_single_intent_questions_are_not_marked_outside_the_corpus():
    r = d("What purchase amount requires competitive bids at Harvard University?")
    assert not r.multi and r.intents[0].outside_corpus == []  # single intent: phase 1 decides, unchanged
