"""Verifier robustness, version attribution, partial answers and rate-limit handling (offline, no network).

Covers the fixes made after the 2026-09-26 integration evaluation:
  * Unicode punctuation / whitespace normalization (U+2011 etc.) in quote matching;
  * list-marker artifacts (bullet glyphs, the Word sub-bullet "o") and explicit skips inside a quote;
  * numbers restated from the question;
  * claims that merge different versions of one document, and version attribution;
  * partial answers ("not_in_sources") instead of abstaining when part of the question is answered;
  * bounded HTTP 429 retries in the integration harness.
"""

import json
from datetime import date

import pytest

from generation import config as C
from generation.answer import NOT_IN_SOURCES_PREFIX, GroundedAnswerer
from generation.context import assemble, version_groups, version_note
from generation.prompt import ANSWER_SCHEMA, SYSTEM_PROMPT, user_message
from generation.providers import ProviderError, ProviderResponse, StubProvider
from generation.validate import names_date, normalize, quote_passages, verify
from tests.test_generation import answered, ev, mk

SERIES = "uni-travel-procedures"


def version(cid, text, doc, effective, superseded=None, current=None):
    c = mk(cid, text, doc=doc, effective=effective, title="Travel Procedures", org="Test University")
    return c.model_copy(update={"series_id": SERIES, "superseded_date": superseded, "is_current": current})


FEB_CARD = version("card", "If card charges are not submitted within 60 days of the trip end date or transaction date, "
                   "the card will be suspended.", "proc-feb", "2026-02-01", superseded="2026-07-01", current=False)
JUL_CARD = version("card", "If card charges are not submitted and fully approved within 60 days of the transaction date "
                   "or the trip end date (whichever is later), the card will be suspended.", "proc-jul", "2026-07-01", current=True)
FEB_QUOTE = "not submitted within 60 days of the trip end date or transaction date"
JUL_QUOTE = "not submitted and fully approved within 60 days of the transaction date or the trip end date (whichever is later)"


def check(ctx, *claims, question="", gaps=None):
    out = answered(*claims)
    if gaps is not None:
        out["not_in_sources"] = gaps
    return verify(json.dumps(out), ctx, question)


# ---------------------------------------------------------------- Unicode normalization


@pytest.mark.parametrize("variant", ["pre‑approved", "pre‐approved", "pre–approved", "pre−approved",
                                     "pre­approved".replace("­", "­-")])
def test_hyphen_variants_match_ascii_hyphen(variant):
    assert normalize(f"a {variant} role") == "a pre-approved role"


def test_quote_with_non_breaking_hyphen_and_typographic_spacing_is_accepted():
    src = mk("h1", "All high-risk travel requests should be submitted at least two months prior to travel.")
    ctx = assemble(ev(src))
    quote = "All high‑risk travel requests should be​ submitted at least two months"
    assert check(ctx, ("High‑risk requests are due two months ahead", ["S1"], [quote])).ok


def test_normalization_does_not_hide_changed_wording():
    ctx = assemble(ev(mk("h2", "Requests should be submitted at least two months prior to travel.")))
    v = check(ctx, ("Requests are due two months ahead", ["S1"], ["Requests must be submitted at least two months"]))
    assert not v.ok and any("quote not found" in p for p in v.problems)


# ---------------------------------------------------------------- list markers and explicit skips


def test_stray_sub_bullet_o_in_source_is_ignored():
    src = mk("o1", "A cost comparison must be provided at the time of booking for the business-only o segment. "
                   "Reimbursement will be for the lesser amount.\nIf no comparison is provided, only 50% will be reimbursed. o")
    ctx = assemble(ev(src))
    clean = "A cost comparison must be provided at the time of booking for the business-only segment."
    assert check(ctx, ("A cost comparison is required at booking", ["S1"], [clean])).ok
    assert check(ctx, ("A cost comparison is required at booking", ["S1"], ["for the business-only o segment"])).ok
    assert normalize("item o\nnext") == "item next" and normalize("go on") == "go on"  # only a standalone "o"


def test_quote_may_skip_list_items_only_at_an_explicit_marker():
    src = mk("b1", "Senior Institutional Official approval is required if:\n• The total trip cost exceeds $7,500\n"
                   "• The trip duration exceeds 21 days")
    ctx = assemble(ev(src))
    claim = "Trips longer than 21 days need Senior Institutional Official approval"
    marked = "Senior Institutional Official approval is required if: • The trip duration exceeds 21 days"
    assert quote_passages(marked) == ["senior institutional official approval is required if:", "the trip duration exceeds 21 days"]
    assert check(ctx, (claim, ["S1"], [marked])).ok
    assert check(ctx, (claim, ["S1"], ["approval is required if: … The trip duration exceeds 21 days"])).ok  # elision
    # bullets in the source do not block a quote of consecutive items
    assert check(ctx, (claim, ["S1"], ["The total trip cost exceeds $7,500 The trip duration exceeds 21 days"])).ok
    # an unmarked splice presents non-adjacent text as continuous: rejected
    v = check(ctx, (claim, ["S1"], ["approval is required if: The trip duration exceeds 21 days"]))
    assert not v.ok and any("quote not found" in p for p in v.problems)


def test_skipped_passages_must_be_in_order_in_one_source_and_substantive():
    a = mk("s1", "Receipts are required for every hotel stay. Meals are reimbursed at per diem.")
    b = mk("s2", "Mileage is reimbursed at the federal rate.", doc="doc-b")
    ctx = assemble(ev(a, b))
    out_of_order = "Meals are reimbursed at per diem … Receipts are required"
    assert not check(ctx, ("Receipts and meals", ["S1"], [out_of_order])).ok
    across = "Receipts are required for every hotel stay … Mileage is reimbursed"
    assert not check(ctx, ("Receipts and mileage", ["S1", "S2"], [across])).ok
    v = check(ctx, ("Receipts and meals", ["S1"], ["are … for … at … per"]))  # no passage of MIN_QUOTE_CHARS
    assert not v.ok and any("no supporting quote" in p for p in v.problems)


# ---------------------------------------------------------------- numbers from the question


def test_number_restated_from_the_question_is_accepted_and_recorded():
    src = mk("q1", "Competitive bids are not required when purchasing from a Preferred Contract Supplier.")
    ctx = assemble(ev(src))
    q = "Does Penn require competitive bids for a $75,000 purchase from a Preferred Contract Supplier?"
    claim = ("Competitive bids are not required for a $75,000 purchase from a Preferred Contract Supplier", ["S1"],
             ["Competitive bids are not required when purchasing from a Preferred Contract Supplier"])
    v = check(ctx, claim, question=q)
    assert v.ok and v.notes == ["claim 1: number(s) ['75000'] restated from the question, not from a source"]
    assert not check(ctx, claim).ok  # without the question the number is unsupported


def test_new_number_is_rejected_even_next_to_a_question_number():
    src = mk("q2", "Informal procurement applies from $25,000.01 to $250,000.00.")
    ctx = assemble(ev(src))
    q = "Oregon State expects a contract price of $180,000. Which method applies?"
    ok = ("A $180,000 contract is in the $25,000.01 to $250,000.00 informal range", ["S1"], ["$25,000.01 to $250,000.00"])
    assert check(ctx, ok, question=q).ok
    bad = ("A $180,000 contract needs 3 quotes", ["S1"], ["$25,000.01 to $250,000.00"])
    v = check(ctx, bad, question=q)
    assert not v.ok and any("number(s) ['3']" in p for p in v.problems)


# ---------------------------------------------------------------- versions


def test_context_lists_versions_for_the_model():
    ctx = assemble(ev(JUL_CARD, FEB_CARD, mk("other", "Unrelated policy text here.")))
    assert version_groups(ctx) == {SERIES: {"proc-feb": ["S2"], "proc-jul": ["S1"]}}
    note = version_note(ctx)
    assert "Test University / Travel Procedures: 2 versions" in note
    assert "version effective 2026-02-01, superseded 2026-07-01: S2" in note and "version effective 2026-07-01, current: S1" in note
    assert user_message("q", ctx).endswith(note)
    single = assemble(ev(JUL_CARD))
    assert version_note(single) == "" and "Document versions" not in user_message("q", single)


def test_merging_conflicting_versions_into_one_claim_is_rejected():
    ctx = assemble(ev(JUL_CARD, FEB_CARD))
    merged = ("Card charges must be submitted within 60 days of the transaction date or trip end date", ["S1", "S2"],
              [JUL_QUOTE, FEB_QUOTE])
    v = check(ctx, merged)
    assert not v.ok and sum("merges versions" in p for p in v.problems) == 2
    # naming both dates does not make a merged claim acceptable
    named = ("Under the procedures effective February 1, 2026 and July 1, 2026, charges are due within 60 days",
             ["S1", "S2"], [JUL_QUOTE, FEB_QUOTE])
    assert not check(ctx, named).ok


def test_version_specific_claims_must_name_their_version():
    ctx = assemble(ev(JUL_CARD, FEB_CARD))
    unnamed = check(ctx, ("Charges must be submitted and fully approved within 60 days, whichever is later", ["S1"], [JUL_QUOTE]),
                    ("Charges must be submitted within 60 days", ["S2"], [FEB_QUOTE]))
    assert not unnamed.ok and sum("must name that version's effective date" in p for p in unnamed.problems) == 2
    named = check(ctx, ("Under the procedures effective July 1, 2026, charges must be submitted and fully approved within 60 days",
                        ["S1"], [JUL_QUOTE]),
                  ("The February 2026 procedures required submission within 60 days", ["S2"], [FEB_QUOTE]))
    assert named.ok, named.problems
    # the wrong version's date does not count
    wrong = check(ctx, ("Under the procedures effective February 1, 2026, approval is also required", ["S1"], [JUL_QUOTE]))
    assert not wrong.ok


def test_one_version_alone_needs_no_attribution_when_the_other_is_absent():
    ctx = assemble(ev(JUL_CARD, mk("x", "Unrelated policy text here.")))
    assert check(ctx, ("Charges must be submitted and fully approved within 60 days", ["S1"], [JUL_QUOTE])).ok


def test_identical_rule_in_both_versions_may_be_stated_once():
    feb = version("r", "Receipts are required for expenses over $75.", "proc-feb", "2026-02-01", superseded="2026-07-01")
    jul = version("r", "Receipts are required for expenses over $75.", "proc-jul", "2026-07-01", current=True)
    ctx = assemble(ev(jul, feb))
    assert ctx.sources[1].duplicate_of == "S1"
    claim = ("Receipts are required for expenses over $75", ["S1", "S2"], ["Receipts are required for expenses over $75"])
    assert check(ctx, claim).ok
    assert check(ctx, ("Receipts are required for expenses over $75", ["S1"], ["Receipts are required for expenses over $75"])).ok
    # same wording inside otherwise different chunks: still common to both versions
    feb2 = version("s", "Spouse travel needs SIO approval. Old routing applies.", "proc-feb", "2026-02-01")
    jul2 = version("s", "Spouse travel needs SIO approval. New Concur routing applies.", "proc-jul", "2026-07-01")
    ctx2 = assemble(ev(jul2, feb2))
    assert check(ctx2, ("Spouse travel needs SIO approval", ["S1", "S2"], ["Spouse travel needs SIO approval"])).ok
    assert not check(ctx2, ("Concur routing applies", ["S1"], ["New Concur routing applies"])).ok  # July-only: name it


def test_citing_a_version_without_quoting_it_is_rejected():
    ctx = assemble(ev(JUL_CARD, FEB_CARD))
    v = check(ctx, ("Under the procedures effective July 1, 2026, charges must be approved within 60 days", ["S1", "S2"], [JUL_QUOTE]))
    assert not v.ok and any("none of its quotes is from that version" in p for p in v.problems)


@pytest.mark.parametrize("text,ok", [
    ("effective 2026-07-01", True), ("the July 1, 2026 procedures", True), ("July 2026", True), ("Jul. 1, 2026", True),
    ("since 1 July", True), ("on July 1st", True), ("7/1/2026", True),
    ("in July", False), ("June 1, 2026", False), ("July 2025", False),
])
def test_names_date(text, ok):
    assert names_date(text, date(2026, 7, 1)) is ok


def test_month_word_alone_is_not_a_version_name():
    assert not names_date("travelers may book", date(2026, 5, 1))
    assert names_date("the May 1, 2026 policy", date(2026, 5, 1))


# ---------------------------------------------------------------- partial answers


LODGE_JUL = version("lodge", "Travelers booking a multi-bedroom accommodation must use a personal credit card.",
                    "proc-jul", "2026-07-01", current=True)
LODGE_Q = "Compare the multi-bedroom accommodation rules in the procedures effective February 1, 2026 and July 1, 2026."


def partial(s, u):
    return {"status": "answered",
            "claims": [{"text": "Under the procedures effective July 1, 2026, multi-bedroom accommodations must be booked with a personal credit card",
                        "sources": ["S1"], "quotes": ["booking a multi-bedroom accommodation must use a personal credit card"]}],
            "not_in_sources": ["the multi-bedroom rule in the procedures effective February 1, 2026"], "abstention_reason": ""}


def test_prompt_and_schema_allow_a_partial_answer():
    assert "not_in_sources" in ANSWER_SCHEMA["required"] and ANSWER_SCHEMA["properties"]["not_in_sources"]["type"] == "array"
    assert '"not_in_sources"' in SYSTEM_PROMPT and "answer the part they support" in SYSTEM_PROMPT


def test_evidence_for_part_of_the_question_gives_a_partial_answer_not_an_abstention():
    a = GroundedAnswerer(StubProvider(partial)).answer(LODGE_Q, ev(LODGE_JUL))
    assert a.status == "answered" and a.abstention_reason is None
    assert a.not_in_sources == ["the multi-bedroom rule in the procedures effective February 1, 2026"]
    assert a.text.endswith(NOT_IN_SOURCES_PREFIX + "the multi-bedroom rule in the procedures effective February 1, 2026.")
    assert [c.doc_id for c in a.citations] == ["proc-jul"]


def test_not_in_sources_cannot_carry_facts_or_accompany_an_abstention():
    ctx = assemble(ev(LODGE_JUL))
    claim = ("Under the procedures effective July 1, 2026, a personal credit card must be used", ["S1"],
             ["must use a personal credit card"])
    assert check(ctx, claim, question=LODGE_Q, gaps=["the February 1, 2026 rule"]).ok
    v = check(ctx, claim, question=LODGE_Q, gaps=["the February rule, which allowed 3 bedrooms"])
    assert not v.ok and any("not_in_sources 1" in p for p in v.problems)
    abst = verify(json.dumps({"status": "insufficient_evidence", "claims": [], "not_in_sources": ["x"],
                              "abstention_reason": "none"}), ctx)
    assert not abst.ok
    assert not verify(json.dumps({"status": "answered", "claims": [], "not_in_sources": "x", "abstention_reason": ""}), ctx).ok


def test_output_without_not_in_sources_still_verifies():  # grounded-qa-1 shaped outputs
    raw = json.dumps({"status": "answered", "claims": [{"text": "A personal credit card must be used", "sources": ["S1"],
                                                        "quotes": ["must use a personal credit card"]}], "abstention_reason": ""})
    v = verify(raw, assemble(ev(LODGE_JUL)))
    assert v.ok and v.not_in_sources == []


# ---------------------------------------------------------------- rate limits (integration harness)


class Flaky:
    name, model = "flaky", "m"

    def __init__(self, errors):
        self.errors, self.calls = list(errors), 0

    def complete(self, system, user, schema):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return ProviderResponse(text="{}", provider="flaky", model="m")


@pytest.fixture
def no_sleep(monkeypatch):
    import evaluation.integration.run as run

    slept = []
    monkeypatch.setattr(run.time, "sleep", slept.append)
    return slept


def test_rate_limit_is_retried_a_bounded_number_of_times(no_sleep):
    from evaluation.integration.run import RATE_LIMIT_BACKOFF_S, RATE_LIMIT_MAX_WAIT_S, RecordingProvider

    inner = Flaky([ProviderError("429", 429, retry_after=5.0), ProviderError("429", 429, retry_after=120.0)])
    rp = RecordingProvider(inner, min_interval=0.0)
    assert rp.complete("s", "u", {}).text == "{}"
    assert inner.calls == 3
    waits = [r["wait_s"] for r in rp.last["rate_limit_retries"]]
    assert waits == [RATE_LIMIT_BACKOFF_S[0], RATE_LIMIT_MAX_WAIT_S]  # max(backoff, Retry-After), capped
    assert rp.last["error"] is None and rp.last["rate_limit_wait_s"] == sum(waits)

    always = Flaky([ProviderError("429", 429)] * 10)
    rp = RecordingProvider(always, min_interval=0.0)
    with pytest.raises(ProviderError):
        rp.complete("s", "u", {})
    assert always.calls == len(RATE_LIMIT_BACKOFF_S) + 1 and rp.last["error"].startswith("ProviderError")


def test_other_provider_errors_are_not_retried(no_sleep):
    from evaluation.integration.run import RecordingProvider

    inner = Flaky([ProviderError("401 bad key", 401)])
    with pytest.raises(ProviderError):
        RecordingProvider(inner, min_interval=0.0).complete("s", "u", {})
    assert inner.calls == 1


def test_exhausted_rate_limit_abstains_and_marks_the_run_incomplete(no_sleep):
    from evaluation.integration.run import RecordingProvider, provider_failures

    rp = RecordingProvider(Flaky([ProviderError("429", 429)] * 10), min_interval=0.0)
    a = GroundedAnswerer(rp).answer("q", ev(mk("c", "Travel Card charges are due in 30 days.")))
    assert (a.status, a.abstention_reason) == ("abstained", "provider_error")
    run = [{"id": "a", "answer": {"abstention_reason": "provider_error"}}, {"id": "b", "answer": {"abstention_reason": None}}]
    assert provider_failures(run) == ["a"]


def test_groq_rate_limit_is_reported_with_status_and_retry_after(monkeypatch):
    import groq
    import httpx

    from generation.providers import get_provider

    class FakeCompletions:
        def create(self, **kw):
            resp = httpx.Response(429, headers={"retry-after": "7"}, request=httpx.Request("POST", "https://api.groq.test"))
            raise groq.RateLimitError("Rate limit reached", response=resp, body=None)

    class FakeGroq:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(groq, "Groq", FakeGroq)
    with pytest.raises(ProviderError) as e:
        get_provider("groq").complete("s", "u", ANSWER_SCHEMA)
    assert e.value.rate_limited and e.value.retry_after == 7.0
    assert C.GROQ_MODEL == "openai/gpt-oss-20b"  # unchanged


# ---------------------------------------------------------------- the question names the version


FEB_Q = "According to the February 2026 Travel Procedures, what happens if card charges are not submitted in time?"
JUL_Q = "Under the travel procedures effective July 1, 2026, when is the card suspended?"
NEUTRAL_Q = "How long do travelers have to submit card charges before the card is suspended?"
ONLY_FEB = {SERIES: ["proc-feb"]}
ONLY_JUL = {SERIES: ["proc-jul"]}


def vcheck(ctx, *claims, question="", versions=None):
    return verify(json.dumps(answered(*claims)), ctx, question, versions)


def test_a_february_question_february_claim_need_not_repeat_the_date():
    ctx = assemble(ev(FEB_CARD, JUL_CARD))  # both versions in context
    claim = ("The card is suspended if charges are not submitted within 60 days", ["S1"], [FEB_QUOTE])
    assert vcheck(ctx, claim, question=FEB_Q, versions=ONLY_FEB).ok
    v = vcheck(ctx, claim, question=FEB_Q)  # the exemption comes from the question's selected version only
    assert not v.ok and any("must name that version's effective date" in p for p in v.problems)


def test_b_july_question_july_claim_need_not_repeat_the_date():
    ctx = assemble(ev(JUL_CARD, FEB_CARD))
    claim = ("The card is suspended if charges are not submitted and fully approved within 60 days", ["S1"], [JUL_QUOTE])
    assert vcheck(ctx, claim, question=JUL_Q, versions=ONLY_JUL).ok
    # a claim from the version the question did NOT select must still name its version
    other = ("Charges must be submitted within 60 days", ["S2"], [FEB_QUOTE])
    v = vcheck(ctx, other, question=JUL_Q, versions=ONLY_JUL)
    assert not v.ok and any("version effective 2026-02-01" in p for p in v.problems)


def test_c_neutral_question_merged_conflicting_claim_is_rejected():
    ctx = assemble(ev(JUL_CARD, FEB_CARD))
    merged = ("Card charges must be submitted within 60 days of the transaction date or trip end date", ["S1", "S2"],
              [JUL_QUOTE, FEB_QUOTE])
    for versions in (None, {}):
        v = vcheck(ctx, merged, question=NEUTRAL_Q, versions=versions)
        assert not v.ok and sum("merges versions" in p for p in v.problems) == 2
    # even when the question names one version, a merge is never accepted
    assert not vcheck(ctx, merged, question=FEB_Q, versions=ONLY_FEB).ok


def test_d_neutral_question_separately_attributed_claims_are_accepted():
    ctx = assemble(ev(JUL_CARD, FEB_CARD))
    v = vcheck(ctx,
               ("Under the procedures effective July 1, 2026, the card is suspended if charges are not submitted and fully "
                "approved within 60 days, whichever is later", ["S1"], [JUL_QUOTE]),
               ("Under the procedures effective February 1, 2026, the card was suspended if charges were not submitted "
                "within 60 days", ["S2"], [FEB_QUOTE]),
               question=NEUTRAL_Q, versions={})
    assert v.ok, v.problems
    # unattributed per-version claims are still rejected for a neutral question
    assert not vcheck(ctx, ("Charges are due within 60 days", ["S1"], [JUL_QUOTE]), question=NEUTRAL_Q, versions={}).ok


def test_compare_question_still_requires_naming_each_version():
    ctx = assemble(ev(JUL_CARD, FEB_CARD))
    both = {SERIES: ["proc-feb", "proc-jul"]}
    assert not vcheck(ctx, ("Charges are due within 60 days", ["S2"], [FEB_QUOTE]), versions=both).ok


def test_e_invented_date_or_misattributed_version_is_rejected():
    ctx = assemble(ev(FEB_CARD, JUL_CARD))
    invented = ("Under the procedures effective March 1, 2026, the card is suspended after 60 days", ["S1"], [FEB_QUOTE])
    v = vcheck(ctx, invented, question=FEB_Q, versions=ONLY_FEB)
    assert not v.ok and any("date(s) ['2026-03-01']" in p for p in v.problems)
    wrong_version = ("Under the procedures effective July 1, 2026, the card is suspended after 60 days", ["S1"], [FEB_QUOTE])
    v = vcheck(ctx, wrong_version, question=FEB_Q, versions=ONLY_FEB)
    assert not v.ok and any("cites only the version effective 2026-02-01" in p for p in v.problems)
    # dates given in the question or the sources are fine
    ok = ("Under the February 2026 procedures, the card is suspended after 60 days", ["S1"], [FEB_QUOTE])
    assert vcheck(ctx, ok, question=FEB_Q, versions=ONLY_FEB).ok
    single = assemble(ev(mk("d1", "The policy takes effect on 15 March 2026 for all travel.")))
    assert vcheck(single, ("The policy takes effect March 15, 2026", ["S1"], ["takes effect on 15 March 2026"])).ok
    assert not vcheck(single, ("The policy takes effect April 15, 2026", ["S1"], ["takes effect on 15 March 2026"])).ok


@pytest.mark.parametrize("text,dates", [
    ("effective 2026-07-01 and 2026-02", {(2026, 7, 1), (2026, 2, None)}), ("on 7/1/2026", {(2026, 7, 1)}),
    ("July 1st, 2026 or Feb. 2026", {(2026, 7, 1), (2026, 2, None)}), ("1 July 2026", {(2026, 7, 1)}),
    ("travelers may book in July", set()), ("$25,000.01 to $250,000.00 and 4.1.7", set()),
])
def test_dates_in(text, dates):
    from generation.validate import dates_in

    assert dates_in(text) == dates


# ---------------------------------------------------------------- one integrated path for CLI and harness


class FakeHybrid:
    """Returns every chunk in list order (stands in for dense + BM25 + RRF)."""

    def __init__(self, chunks):
        self.chunks = chunks

    def fuse(self, query):
        from retrieval.hybrid import Fused

        return [Fused(i, 1.0 / (i + 1), {"dense": i + 1}) for i in range(len(self.chunks))]


class FakeCrossEncoder:
    def predict(self, pairs, **kw):
        import numpy as np

        return np.array([5.0 - i for i in range(len(pairs))])


def fake_stack():
    from generation.pipeline import RetrievalStack
    from retrieval.rerank import RerankedHybridRetriever
    from temporal.resolve import TemporalResolver

    chunks = [JUL_CARD, FEB_CARD, mk("x", "Unrelated policy text here.")]
    hybrid = FakeHybrid(chunks)
    return RetrievalStack(chunks, hybrid, RerankedHybridRetriever(hybrid, FakeCrossEncoder()),
                          TemporalResolver.from_chunks(chunks))


def test_integrated_retrieval_applies_temporal_resolution_before_reranking():
    from generation.pipeline import retrieve

    r = retrieve(fake_stack(), FEB_Q)
    assert r.question_versions == ONLY_FEB
    assert [e.chunk.doc_id for e in r.evidence] == ["proc-feb", "doc-a"]  # the July version was removed
    assert r.resolution.dropped == [JUL_CARD.chunk_id] and set(r.seconds) == {"hybrid_retrieval_rrf", "temporal", "rerank"}
    neutral = retrieve(fake_stack(), NEUTRAL_Q)
    assert neutral.question_versions == {} and [e.chunk.doc_id for e in neutral.evidence] == ["proc-jul", "proc-feb", "doc-a"]


def test_cli_ask_uses_the_integrated_path_and_question_version():
    from generation.pipeline import ask

    stub = StubProvider(lambda s, u: answered(
        ("The card is suspended if charges are not submitted within 60 days", ["S1"], [FEB_QUOTE])))
    a = ask(fake_stack(), stub, FEB_Q)
    assert a.status == "answered", a.verification_problems
    assert [c.doc_id for c in a.citations] == ["proc-feb"]
    assert "proc-jul" not in stub.calls[0][1]  # the model never saw the other version


def test_integration_harness_uses_the_same_functions_as_the_cli():
    import inspect

    import evaluation.integration.run as run
    import generation.__main__ as cli
    from generation import pipeline

    src = inspect.getsource(run.run_case)
    assert "retrieve(stack, q)" in src and "answer_retrieval(provider, r)" in src
    assert "fuse(" not in src and ".rerank(" not in src and "resolve(" not in src  # no second implementation
    assert "ask(" in inspect.getsource(cli.main)
    assert "answer_retrieval(provider, retrieve(stack, query))" in inspect.getsource(pipeline.ask)
