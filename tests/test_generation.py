"""Grounded generation (offline): context assembly, verification, citations, abstention, provider adapter.

Uses a scripted StubProvider; no network. Live-model checks are in evaluation/generation (run separately).
"""

import hashlib
import json

import pytest

from chunking.models import Chunk
from generation import config as C
from generation.answer import GroundedAnswerer
from generation.context import Evidence, assemble, render_sources, source_header
from generation.prompt import ANSWER_SCHEMA, SYSTEM_PROMPT, user_message
from generation.providers import ProviderError, StubProvider, get_provider
from generation.validate import numbers_in, verify


def mk(cid, text, org="Test University", title="Travel Policy", pages=(1, 1), section=("Travel",), clause=None,
       effective=None, doc="doc-a"):
    return Chunk(
        chunk_id=f"{doc}::{cid}", doc_id=doc, ordinal=0, organization=org, title=title, domain="travel_expenses",
        document_type="policy", authority_level="authoritative", version=None, issued_date=None,
        effective_date=effective, superseded_date=None, is_current=None, series_id=None, source_url=None,
        capture_date=None, section_path=list(section), section_ids=[], clause_id=clause, clause_ids=[],
        page_start=pages[0], page_end=pages[1], pages=list(range(pages[0], pages[1] + 1)), source_block_ids=[],
        source_line_ids=[], text=text, context_header="", retrieval_text=text, token_count=len(text.split()),
        retrieval_token_count=len(text.split()), content_hash=hashlib.sha256(text.encode()).hexdigest(), split="structural",
    )


CARD = mk("c1", "Travel Card charges must be submitted within 30 days of the trip end date.", section=("Expense Reporting",),
          clause="4.2", pages=(5, 6))
ADV = mk("c2", "Advance requests are limited to 75% of estimated expenses and must exceed $500.", section=())
FREIGHT = mk("c3", "Stanford must pay all freight bills within seven (7) days of receipt.", org="Stanford University")


def ev(*chunks, rerank=None):
    return [Evidence(c, i, None, "test", rerank[i - 1] if rerank else None) for i, c in enumerate(chunks, 1)]


def answered(*claims):
    return {"status": "answered", "claims": [{"text": t, "sources": s, "quotes": q} for t, s, q in claims],
            "abstention_reason": ""}


# ---------------------------------------------------------------- context assembly


def test_context_keeps_retrieval_order_and_metadata():
    ctx = assemble(ev(CARD, ADV, FREIGHT))
    assert [s.label for s in ctx.sources] == ["S1", "S2", "S3"]
    assert [s.chunk.chunk_id for s in ctx.sources] == [CARD.chunk_id, ADV.chunk_id, FREIGHT.chunk_id]
    h = source_header(ctx.sources[0])
    assert h == ("[S1] | organization: Test University | document: Travel Policy | type: policy | "
                 "section: Expense Reporting | clause: 4.2 | pages: 5-6 | chunk: doc-a::c1")
    assert "section:" not in source_header(ctx.sources[1]) and "clause:" not in source_header(ctx.sources[1])
    assert CARD.text in render_sources(ctx)  # verbatim


def test_context_drops_repeated_chunks_and_shares_identical_text():
    feb = mk("p1", "Rentals are reimbursed up to an intermediate car class.", doc="uconn-feb", effective="2026-02-01")
    jul = mk("p1", "Rentals are reimbursed up to an intermediate car class.", doc="uconn-jul", effective="2026-07-01")
    ctx = assemble(ev(feb, feb, jul))
    assert [s.chunk.doc_id for s in ctx.sources] == ["uconn-feb", "uconn-jul"]
    assert ctx.sources[1].duplicate_of == "S1"
    rendered = render_sources(ctx)
    assert rendered.count("intermediate car class") == 1 and "(identical text to [S1]; not repeated)" in rendered
    assert "effective: 2026-07-01" in rendered  # both versions stay identifiable
    assert ctx.skipped == [(feb.chunk_id, "repeated chunk")]


def test_context_budget_skips_whole_chunks_never_truncates():
    big = mk("big", "word " * 3000)
    ctx = assemble(ev(big, CARD), max_tokens=100)
    assert [s.chunk.chunk_id for s in ctx.sources] == [CARD.chunk_id]
    assert ctx.skipped == [(big.chunk_id, "token budget")]
    many = [mk(f"m{i}", f"rule {i} text") for i in range(10)]
    assert len(assemble(ev(*many)).sources) == C.MAX_SOURCES


# ---------------------------------------------------------------- verification


def test_verify_accepts_grounded_answer():
    ctx = assemble(ev(CARD, ADV))
    raw = json.dumps(answered(("Charges must be submitted within 30 days of the trip end date", ["S1"],
                               ["submitted within 30 days of the trip end date"])))
    v = verify(raw, ctx)
    assert v.ok and v.status == "answered" and v.claims[0].sources == ["S1"]


@pytest.mark.parametrize("claim,problem", [
    (("Charges are due in 30 days", ["S9"], ["submitted within 30 days"]), "unknown source"),
    (("Charges are due in 30 days", [], ["submitted within 30 days"]), "no citation"),
    (("Charges are due in 30 days", ["S1"], ["due within thirty business days"]), "quote not found"),
    (("Charges are due in 30 days", ["S1"], ["30 days"]), "no supporting quote"),
    (("Charges are due in 45 days", ["S1"], ["submitted within 30 days"]), "number(s) ['45']"),
    (("Charges are due in 30 days", ["S2"], ["limited to 75% of estimated expenses"]), "number(s) ['30']"),
])
def test_verify_rejects_ungrounded_claims(claim, problem):
    v = verify(json.dumps(answered(claim)), assemble(ev(CARD, ADV)))
    assert not v.ok and any(problem in p for p in v.problems)


def test_verify_numbers_accept_words_amounts_and_duplicates():
    assert numbers_in("$25,000.00 and 4.4.3.1 and 07") == {"25000", "4.4.3.1", "7"}
    ctx = assemble(ev(FREIGHT))
    ok = answered(("Freight bills must be paid within 7 days", ["S1"], ["within seven (7) days of receipt"]))
    assert verify(json.dumps(ok), ctx).ok
    feb = mk("x", "Receipts are required for expenses over $50.", doc="feb")
    jul = mk("x", "Receipts are required for expenses over $50.", doc="jul")
    dctx = assemble(ev(feb, jul))
    both = answered(("Receipts are required over $50 in both versions", ["S1", "S2"], ["Receipts are required for expenses over $50"]))
    assert verify(json.dumps(both), dctx).ok
    only_dup = answered(("Receipts are required over $50", ["S2"], ["Receipts are required for expenses over $50"]))
    assert verify(json.dumps(only_dup), dctx).ok  # the duplicate source quotes the text it stands for


@pytest.mark.parametrize("raw", ["not json", "[]", json.dumps({"status": "maybe", "claims": []}),
                                 json.dumps(answered()),
                                 json.dumps({"status": "insufficient_evidence", "claims": [{"text": "x", "sources": [], "quotes": []}],
                                             "abstention_reason": "none"})])
def test_verify_rejects_malformed_or_inconsistent_output(raw):
    assert not verify(raw, assemble(ev(CARD))).ok


# ---------------------------------------------------------------- answering, citations, abstention


def test_answer_with_citations_traceable_to_chunks():
    stub = StubProvider(lambda s, u: answered(
        ("Travel Card charges must be submitted within 30 days of the trip end date", ["S1"],
         ["charges must be submitted within 30 days of the trip end date"]),
        ("Advance requests must exceed $500", ["S2"], ["must exceed $500"])))
    a = GroundedAnswerer(stub).answer("When are card charges due and when can I get an advance?", ev(CARD, ADV, FREIGHT))
    assert a.status == "answered"
    assert a.text == ("Travel Card charges must be submitted within 30 days of the trip end date.[1] "
                      "Advance requests must exceed $500.[2]")
    c1, c2 = a.citations
    assert (c1.chunk_id, c1.organization, c1.document, c1.pages, c1.section, c1.clause) == (
        CARD.chunk_id, "Test University", "Travel Policy", "pp. 5-6", "Expense Reporting", "4.2")
    assert c2.section is None and c2.clause is None  # absent metadata is omitted, never invented
    assert c1.render() == "[1] Test University — Travel Policy, Expense Reporting, clause 4.2, pp. 5-6 · chunk doc-a::c1"
    assert c2.render() == "[2] Test University — Travel Policy, p. 1 · chunk doc-a::c2"
    assert a.claims[1]["citations"] == [2]
    system, user = stub.calls[0]
    assert system == SYSTEM_PROMPT and user == user_message(a.question, assemble(ev(CARD, ADV, FREIGHT)))


def test_model_insufficient_evidence_gives_fixed_abstention():
    stub = StubProvider(lambda s, u: {"status": "insufficient_evidence", "claims": [], "abstention_reason": "not covered"})
    a = GroundedAnswerer(stub).answer("What is the pet boarding policy?", ev(CARD))
    assert (a.status, a.text, a.abstention_reason) == ("abstained", C.ABSTENTION_TEXT, "model_insufficient_evidence")
    assert a.citations == [] and a.abstention_detail == "not covered"


def test_no_evidence_abstains_without_calling_the_model():
    stub = StubProvider(lambda s, u: pytest.fail("model must not be called"))
    a = GroundedAnswerer(stub).answer("Anything?", [])
    assert (a.status, a.abstention_reason) == ("abstained", "no_evidence") and stub.calls == []


def test_low_rerank_scores_abstain_without_calling_the_model():
    stub = StubProvider(lambda s, u: pytest.fail("model must not be called"))
    a = GroundedAnswerer(stub).answer("Unrelated question", ev(CARD, ADV, rerank=[-3.1, -7.0]))
    assert a.abstention_reason == "low_relevance" and stub.calls == []
    ok = StubProvider(lambda s, u: {"status": "insufficient_evidence", "claims": [], "abstention_reason": "x"})
    assert GroundedAnswerer(ok).answer("q", ev(CARD, rerank=[0.4])).abstention_reason == "model_insufficient_evidence"


def test_hallucinated_number_is_never_shown():
    stub = StubProvider(lambda s, u: answered(("Card charges are due within 45 days", ["S1"], ["charges must be submitted within 30 days"])))
    a = GroundedAnswerer(stub).answer("When are card charges due?", ev(CARD))
    assert a.status == "abstained" and a.abstention_reason == "ungrounded_output"
    assert "45" not in a.text and a.raw_output and a.verification_problems


def test_provider_failure_abstains():
    def boom(s, u):
        raise ProviderError("RateLimitError: slow down")

    a = GroundedAnswerer(StubProvider(boom)).answer("q", ev(CARD))
    assert (a.status, a.abstention_reason) == ("abstained", "provider_error")


def test_conflicting_versions_are_cited_separately():
    feb = mk("pen", "If charges are not submitted within 60 days of the trip end date or transaction date, the card will be suspended.",
             doc="uconn-feb", effective="2026-02-01", title="Travel and Entertainment Procedures")
    jul = mk("pen", "If charges are not submitted and fully approved within 60 days of the transaction date or the trip end date "
             "(whichever is later), the card will be suspended.", doc="uconn-jul", effective="2026-07-01",
             title="Travel and Entertainment Procedures")
    stub = StubProvider(lambda s, u: answered(
        ("Under the February 2026 procedures the card is suspended if charges are not submitted within 60 days of the trip end date or transaction date",
         ["S1"], ["not submitted within 60 days of the trip end date or transaction date"]),
        ("Under the July 2026 procedures charges must be submitted and fully approved within 60 days, whichever date is later",
         ["S2"], ["submitted and fully approved within 60 days", "(whichever is later)"])))
    a = GroundedAnswerer(stub).answer("When is a UConn travel card suspended?", ev(feb, jul))
    assert a.status == "answered" and [c.doc_id for c in a.citations] == ["uconn-feb", "uconn-jul"]
    assert [c.effective_date for c in a.citations] == ["2026-02-01", "2026-07-01"]
    assert "(effective 2026-07-01)" in a.citations[1].render()


# ---------------------------------------------------------------- provider adapter


def test_groq_provider_sends_strict_schema_and_pinned_settings(monkeypatch):
    import groq

    captured = {}

    class FakeCompletions:
        def create(self, **kw):
            captured.update(kw)
            msg = type("M", (), {"content": json.dumps({"status": "insufficient_evidence", "claims": [], "abstention_reason": "x"})})
            choice = type("Ch", (), {"message": msg, "finish_reason": "stop"})
            return type("R", (), {"choices": [choice], "model": kw["model"], "usage": None, "system_fingerprint": "fp_1"})

    class FakeGroq:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(groq, "Groq", FakeGroq)
    p = get_provider("groq")
    r = p.complete("sys", "user", ANSWER_SCHEMA)
    assert captured["model"] == C.GROQ_MODEL == "openai/gpt-oss-20b"
    assert captured["temperature"] == 0.0 and captured["seed"] == C.SEED
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert captured["response_format"]["json_schema"]["schema"] == ANSWER_SCHEMA
    assert captured["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "user"}]
    assert (r.provider, r.model, r.system_fingerprint) == ("groq", C.GROQ_MODEL, "fp_1")
    with pytest.raises(ValueError):
        get_provider("nope")


def test_schema_is_strict_compatible():
    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False and set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])

    walk(ANSWER_SCHEMA)


def test_numbers_from_metadata_allowed_but_not_from_chunk_ids():
    jul = mk("0212-deadbeef", "Travelers must purchase non-refundable tickets.", effective="2026-07-01", clause="PR7.1")
    ctx = assemble(ev(jul))
    ok = answered(("The July 2026 procedures require non-refundable tickets", ["S1"], ["must purchase non-refundable tickets"]))
    assert verify(json.dumps(ok), ctx).ok
    bad = answered(("Tickets must be bought 212 days ahead", ["S1"], ["must purchase non-refundable tickets"]))
    v = verify(json.dumps(bad), ctx)
    assert not v.ok and any("['212']" in p for p in v.problems)
