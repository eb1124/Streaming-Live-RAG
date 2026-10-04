"""Adaptive retrieval controller (offline): decision, per-version split, combination, frozen path unchanged.

Uses the fake retrieval stack and StubProvider patterns of tests/test_generation_verification.py; no network.
"""

import inspect
import json

import pytest

from adaptive.controller import SINGLE, SPLIT, VERSION_ATTRIBUTION_PROBLEMS, AdaptiveController
from generation import config as C
from generation.answer import GroundedAnswerer
from generation.context import assemble
from generation.pipeline import RetrievalStack, ask, retrieve
from generation.providers import StubProvider
from generation.validate import verify
from retrieval.rerank import RerankedHybridRetriever
from temporal.resolve import TemporalResolver
from tests.test_generation import answered, ev, mk
from tests.test_generation_verification import (FEB_CARD, FEB_Q, FEB_QUOTE, JUL_CARD, JUL_QUOTE, NEUTRAL_Q, SERIES,
                                                FakeCrossEncoder, FakeHybrid, version)

OTHER = mk("x", "Unrelated policy text here about something else entirely.")
SHARED = mk("rcpt", "Receipts are required for every expense over $75.", doc="doc-r", title="Receipt Rules")
FEB_SAME = version("sio", "Trips longer than 21 days need Senior Institutional Official approval.", "proc-feb", "2026-02-01",
                   superseded="2026-07-01", current=False)
JUL_SAME = version("sio", "Trips longer than 21 days need Senior Institutional Official approval.", "proc-jul", "2026-07-01",
                   current=True)
# same rule, different chunk text elsewhere (hashes differ): the neu-sio / spouse / personal-leg situation
FEB_MIXED = version("sio", "Trips longer than 21 days need Senior Institutional Official approval. Old routing form.",
                    "proc-feb", "2026-02-01", superseded="2026-07-01", current=False)
JUL_MIXED = version("sio", "Trips longer than 21 days need Senior Institutional Official approval. New Concur routing.",
                    "proc-jul", "2026-07-01", current=True)
SIO_QUOTE = "Trips longer than 21 days need Senior Institutional Official approval"

FEB_LABEL = "Test University Travel Procedures, version effective 2026-02-01"
JUL_LABEL = "Test University Travel Procedures, version effective 2026-07-01"


def stack(*chunks):
    hybrid = FakeHybrid(list(chunks))
    return RetrievalStack(list(chunks), hybrid, RerankedHybridRetriever(hybrid, FakeCrossEncoder()),
                          TemporalResolver.from_chunks(list(chunks)))


def has(user, chunk):
    return chunk.chunk_id in user


def card_script(feb=None, jul=None):
    """Both versions in the prompt -> a merged claim (what gpt-oss did on i-uc-neutral-card).
    One version -> that version's reply (default: its rule, no date in the claim text)."""

    def script(system, user):
        if has(user, FEB_CARD) and has(user, JUL_CARD):
            return answered(("Card charges must be submitted within 60 days", ["S1", "S2"], [JUL_QUOTE, FEB_QUOTE]))
        if has(user, FEB_CARD):
            return feb or answered(("The card is suspended if charges are not submitted within 60 days", ["S1"], [FEB_QUOTE]))
        if has(user, JUL_CARD):
            return jul or answered(("The card is suspended if charges are not submitted and fully approved within 60 days",
                                    ["S1"], [JUL_QUOTE]))
        return {"status": "insufficient_evidence", "claims": [], "not_in_sources": [], "abstention_reason": "none"}

    return script


def insufficient(reason="not stated"):
    return {"status": "insufficient_evidence", "claims": [], "not_in_sources": [], "abstention_reason": reason}


# ---------------------------------------------------------------- decision: single


def test_1_single_version_in_context_is_single():
    stub = StubProvider(card_script())
    a = AdaptiveController(stack(JUL_CARD, OTHER), stub).run(NEUTRAL_Q)
    assert a.decision.strategy == SINGLE and a.status == "answered" and len(stub.calls) == 1
    assert a.decision.signals["versions_in_context"] == {}
    assert "no document has two or more versions" in a.decision.reason


def test_2_version_specific_question_is_single():
    stub = StubProvider(card_script())
    a = AdaptiveController(stack(JUL_CARD, FEB_CARD, OTHER), stub).run(FEB_Q)
    assert a.decision.strategy == SINGLE and len(stub.calls) == 1
    assert a.decision.signals["intent"] == "point_in_time" and a.decision.signals["selected_versions"] == {SERIES: ["proc-feb"]}
    assert [c.doc_id for c in a.citations] == ["proc-feb"] and "proc-jul" not in stub.calls[0][1]


def test_3_identical_evidence_in_both_versions_is_single():
    stub = StubProvider(lambda s, u: answered(("Trips over 21 days need SIO approval", ["S1", "S2"], [SIO_QUOTE])))
    a = AdaptiveController(stack(JUL_SAME, FEB_SAME, OTHER), stub).run("Which trips need SIO approval?")
    assert a.decision.strategy == SINGLE and len(stub.calls) == 1 and a.status == "answered"
    assert a.decision.signals["series_with_differing_evidence"] == []
    assert "identical evidence" in a.decision.reason


def test_3b_differing_chunks_but_shared_rule_accepted_stays_single():
    """Hashes differ (other lines changed), but the model quoted text common to both versions: accepted, no split."""
    stub = StubProvider(lambda s, u: answered(("Trips over 21 days need SIO approval", ["S1", "S2"], [SIO_QUOTE])))
    a = AdaptiveController(stack(JUL_MIXED, FEB_MIXED, OTHER), stub).run("Which trips need SIO approval?")
    assert a.decision.signals["series_with_differing_evidence"] == [SERIES]
    assert a.decision.strategy == SINGLE and "accepted" in a.decision.reason and len(stub.calls) == 1
    assert a.status == "answered" and a.single_attempt is None and a.parts == []


# ---------------------------------------------------------------- decision: split


def test_4_neutral_question_with_differing_versions_rejected_for_attribution_splits():
    stub = StubProvider(card_script())
    a = AdaptiveController(stack(JUL_CARD, FEB_CARD, OTHER), stub).run(NEUTRAL_Q)
    assert a.decision.strategy == SPLIT and len(stub.calls) == 3  # single attempt + one per version
    assert a.status == "answered"
    rejected = a.single_attempt
    assert rejected["status"] == "abstained" and rejected["abstention_reason"] == "ungrounded_output"
    assert all("merges versions" in p for p in rejected["verification_problems"])
    assert a.decision.signals["single_version_problems"] == rejected["verification_problems"]


def test_5_each_split_call_sees_only_its_version():
    stub = StubProvider(card_script())
    AdaptiveController(stack(JUL_CARD, FEB_CARD, OTHER), stub).run(NEUTRAL_Q)
    single, first, second = (u for _, u in stub.calls)
    assert has(single, FEB_CARD) and has(single, JUL_CARD)
    assert has(first, FEB_CARD) and not has(first, JUL_CARD) and has(first, OTHER)  # other documents are kept
    assert has(second, JUL_CARD) and not has(second, FEB_CARD) and has(second, OTHER)
    assert "Document versions" not in first and "Document versions" not in second


def test_6_each_part_gets_its_question_versions(monkeypatch):
    seen = []
    original = GroundedAnswerer.answer

    def spy(self, question, evidence, question_versions=None):
        seen.append((sorted({e.chunk.doc_id for e in evidence}), question_versions))
        return original(self, question, evidence, question_versions=question_versions)

    monkeypatch.setattr(GroundedAnswerer, "answer", spy)
    AdaptiveController(stack(JUL_CARD, FEB_CARD, OTHER), StubProvider(card_script())).run(NEUTRAL_Q)
    assert seen == [(["doc-a", "proc-feb", "proc-jul"], {}),  # the frozen single path: neutral, nothing selected
                    (["doc-a", "proc-feb"], {SERIES: ["proc-feb"]}),
                    (["doc-a", "proc-jul"], {SERIES: ["proc-jul"]})]


def test_7_version_labels_come_from_metadata_not_model_text():
    a = AdaptiveController(stack(JUL_CARD, FEB_CARD, OTHER), StubProvider(card_script())).run(NEUTRAL_Q)
    feb, jul = a.text.split("\n")
    assert feb.startswith(FEB_LABEL + ": ") and jul.startswith(JUL_LABEL + ": ")  # effective-date order
    # the model's own claim text carries no date; it is unchanged inside the section
    assert "The card is suspended if charges are not submitted within 60 days.[1]" in feb
    assert [p["label"] for p in a.parts] == [FEB_LABEL, JUL_LABEL]
    assert [(c["version"], c["text"]) for c in a.claims] == [
        ("proc-feb", "The card is suspended if charges are not submitted within 60 days"),
        ("proc-jul", "The card is suspended if charges are not submitted and fully approved within 60 days")]


def test_8_citations_are_renumbered_across_parts_and_shared_sources_keep_one_number():
    def script(system, user):
        if has(user, FEB_CARD) and has(user, JUL_CARD):
            return answered(("Card charges must be submitted within 60 days", ["S1", "S2"], [JUL_QUOTE, FEB_QUOTE]))
        own = FEB_QUOTE if has(user, FEB_CARD) else JUL_QUOTE
        # context order in each part: S1 = the version's card chunk, S2 = the receipts document
        return answered(("Receipts are needed over $75", ["S2"], ["Receipts are required for every expense over $75"]),
                        ("The card is suspended after 60 days", ["S1"], [own]))

    a = AdaptiveController(stack(JUL_CARD, FEB_CARD, SHARED), StubProvider(script)).run(NEUTRAL_Q)
    assert a.decision.strategy == SPLIT and a.status == "answered"
    assert [(c.number, c.doc_id) for c in a.citations] == [(1, "doc-r"), (2, "proc-feb"), (3, "proc-jul")]
    assert [c["citations"] for c in a.claims] == [[1], [2], [1], [3]]
    feb, jul = a.text.split("\n")
    assert "over $75.[1]" in feb and "after 60 days.[2]" in feb and "over $75.[1]" in jul and "after 60 days.[3]" in jul
    by_number = {c.number: c.chunk_id for c in a.citations}
    assert by_number[2] == FEB_CARD.chunk_id and by_number[3] == JUL_CARD.chunk_id


# ---------------------------------------------------------------- abstention


def test_9_one_version_abstains_the_other_is_answered_with_a_gap():
    a = AdaptiveController(stack(JUL_CARD, FEB_CARD, OTHER), StubProvider(card_script(feb=insufficient()))).run(NEUTRAL_Q)
    assert a.decision.strategy == SPLIT and a.status == "answered"
    feb, jul = a.text.split("\n")
    assert feb == f"{FEB_LABEL}: the retrieved sources for this version do not answer the question."
    assert jul.startswith(JUL_LABEL + ": The card is suspended")
    assert [c.doc_id for c in a.citations] == ["proc-jul"] and [c["version"] for c in a.claims] == ["proc-jul"]
    assert a.not_in_sources == [f"{FEB_LABEL}: no verified answer (model_insufficient_evidence)"]


def test_10_both_versions_abstain_gives_the_fixed_abstention():
    stub = StubProvider(card_script(feb=insufficient(), jul=insufficient()))
    a = AdaptiveController(stack(JUL_CARD, FEB_CARD, OTHER), stub).run(NEUTRAL_Q)
    assert a.decision.strategy == SPLIT and a.status == "abstained" and a.text == C.ABSTENTION_TEXT
    assert a.abstention_reason == "model_insufficient_evidence" and a.citations == [] and a.claims == []
    assert FEB_LABEL in a.abstention_detail and JUL_LABEL in a.abstention_detail


# ---------------------------------------------------------------- the verifier stays in force


def test_11_verifier_still_rejects_a_part_and_nothing_unverified_is_shown():
    invented = answered(("The card is suspended after 45 days", ["S1"], [FEB_QUOTE]))  # 45 is in no source
    a = AdaptiveController(stack(JUL_CARD, FEB_CARD, OTHER), StubProvider(card_script(feb=invented))).run(NEUTRAL_Q)
    assert a.decision.strategy == SPLIT and "45" not in a.text
    feb_part = a.parts[0]["answer"]
    assert feb_part["abstention_reason"] == "ungrounded_output"
    assert any("number(s) ['45']" in p for p in a.verification_problems)


def test_11b_rejection_for_other_reasons_does_not_split():
    """Only a rejection caused solely by version attribution splits (e.g. not a quote-format failure)."""
    def script(system, user):
        return answered(("Charges are due within 60 days", ["S1", "S2"], ['"' + JUL_QUOTE + '"']))

    stub = StubProvider(script)
    a = AdaptiveController(stack(JUL_CARD, FEB_CARD, OTHER), stub).run(NEUTRAL_Q)
    assert a.decision.strategy == SINGLE and len(stub.calls) == 1 and a.status == "abstained"
    assert "not rejected for version attribution alone" in a.decision.reason


def test_11c_version_attribution_markers_match_the_real_verifier():
    ctx = assemble(ev(JUL_CARD, FEB_CARD))
    merged = verify(json.dumps(answered(("Charges are due within 60 days", ["S1", "S2"], [JUL_QUOTE, FEB_QUOTE]))), ctx)
    unnamed = verify(json.dumps(answered(("Charges are due within 60 days", ["S1"], [JUL_QUOTE]))), ctx)
    wrong = verify(json.dumps(answered(("Under the procedures effective February 1, 2026, charges are due", ["S1"],
                                        [JUL_QUOTE]))), ctx)
    for v in (merged, unnamed):
        assert v.problems and all(any(m in p for m in VERSION_ATTRIBUTION_PROBLEMS) for p in v.problems), v.problems
    # the wrong-version marker is recognized; the claim also names a date its source lacks, so it is not
    # a rejection for attribution alone and the controller would not split on it
    assert any("but cites only" in p for p in wrong.problems)
    assert not all(any(m in p for m in VERSION_ATTRIBUTION_PROBLEMS) for p in wrong.problems)


# ---------------------------------------------------------------- the frozen path is unchanged


@pytest.mark.parametrize("query,chunks", [(FEB_Q, (JUL_CARD, FEB_CARD, OTHER)), (NEUTRAL_Q, (JUL_CARD, OTHER)),
                                          ("Which trips need SIO approval?", (JUL_SAME, FEB_SAME, OTHER))])
def test_12_single_strategy_returns_exactly_what_ask_returns(query, chunks):
    def script(system, user):
        if has(user, JUL_SAME):
            return answered(("Trips over 21 days need SIO approval", ["S1", "S2"], [SIO_QUOTE]))
        return card_script()(system, user)

    frozen = ask(stack(*chunks), StubProvider(script), query).to_dict()
    adaptive = AdaptiveController(stack(*chunks), StubProvider(script)).run(query)
    assert adaptive.decision.strategy == SINGLE
    d = adaptive.to_dict()
    assert {k: d[k] for k in frozen} == frozen


def test_12b_frozen_entry_points_are_untouched():
    import adaptive.__main__ as adaptive_cli
    import generation.__main__ as cli
    from generation import pipeline

    assert "answer_retrieval(provider, retrieve(stack, query))" in inspect.getsource(pipeline.ask)
    assert "ask(" in inspect.getsource(cli.main) and "adaptive" not in inspect.getsource(cli)
    assert "controller.run(" in inspect.getsource(adaptive_cli.main)
    src = inspect.getsource(AdaptiveController)
    assert "answer_retrieval(self.provider, r)" in src and "retrieve(self.stack, query)" in src
    assert "fuse(" not in src and ".rerank(" not in src and "resolve(" not in src and "complete(" not in src


def test_controller_answer_reuses_a_recorded_retrieval():
    s = stack(JUL_CARD, FEB_CARD, OTHER)
    r = retrieve(s, NEUTRAL_Q)
    a = AdaptiveController(s, StubProvider(card_script())).answer(r)
    assert a.decision.strategy == SPLIT and a.decision.signals["intent"] == "neutral"
