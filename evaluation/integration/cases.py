"""End-to-end integration suite (separate from the frozen 69-case retrieval benchmark, the 18-case temporal
suite and the 13-case generation suite; none of those is used or changed here).

Every case runs the complete pipeline on the live corpus:
  query -> dense + BM25 -> RRF -> temporal resolution -> top-20 pool -> cross-encoder rerank -> top 10
        -> context assembly -> grounded generation (Groq) -> deterministic verification -> answer | abstention

Case fields (chunks are referenced by verbatim quotes, resolved to exactly one chunk of the named document):
  question    the query, verbatim
  tags        what the case exercises (see TAGS)
  expect      "answer" | "abstain"
  orgs        organizations whose documents may legitimately be cited (None = no answer expected)
  intent      expected temporal intent: neutral | point_in_time | current | compare
  versions    expected selected UConn procedures version(s): None (series not resolved), [] (no version
              available), ["feb"], ["jul"] or ["feb", "jul"]
  gold        evidence units needed for a correct answer (or, for "abstain" cases, the most relevant
              evidence the corpus has); each unit is a list of acceptable chunks (any one suffices)
  cite        evidence units the answer must cite (each: any one of its chunks)
  forbidden   chunks that must not reach the context or the citations (wrong version / wrong organization)
  facts       regexes the answer text must match (explicit expected-answer check, case-insensitive)
  wrong       regexes the answer text must NOT match (known misstatements)
  expected    the correct answer in prose, for manual review
Expected answers were written from the source text before any integration run.
"""

from evaluation.retrieval.cases import DOCS


def ref(doc: str, quote: str) -> dict:
    return {"doc": doc, "quote": quote}


def both(quote: str) -> list[dict]:
    """Identical text in the February and July UConn procedures: either version is acceptable."""
    return [ref("uconn_feb", quote), ref("uconn_jul", quote)]


TAGS = {
    "factual", "numeric", "conditional", "exception", "version_sensitive", "current", "version_neutral",
    "compare", "temporal_filter", "multi_chunk", "cross_section", "exact_citation", "unanswerable",
    "wrong_org", "evidence_but_insufficient", "prior_generation_failure", "prior_temporal_rerank_weakness",
}

PEN_FEB = ref("uconn_feb", "submitted within 60 days of the trip end date or transaction date, the card will be suspended")
PEN_JUL = ref("uconn_jul", "the cardholder has submitted the Kuali Build form requesting reinstatement")
LODGE_FEB = ref("uconn_feb", "Lodging costs may exceed the federal per diem rate by more than 50%")
LODGE_JUL = ref("uconn_jul", "Travelers booking a multi-bedroom accommodation must use a personal credit card")
FRONT_FEB = ref("uconn_feb", "Approval Date: November 19, 2025")
FRONT_JUL = ref("uconn_jul", "Approval Date: June 17, 2026")
AIR_FEB = ref("uconn_feb", "If a personal leg or segment is added")
AIR_JUL = ref("uconn_jul", "Travelers must purchase non-refundable tickets")
ADV_FEB = ref("uconn_feb", "submit it at least 20 days prior to departure")
ADV_JUL = ref("uconn_jul", "submit it at least 20 days prior to departure")
ADV_POLICY = ref("uconn_policy", "Advance requests are limited to 75% of estimated expenses")

CASES = [
    # ------------------------------------------------------------------ UConn: explicit versions
    dict(id="i-uc-jul-submit", tags=["version_sensitive", "numeric", "temporal_filter"],
         question="Under UConn's travel procedures effective July 1, 2026, within how many days must University Travel Card charges be submitted, and when is the card suspended?",
         expect="answer", orgs=["University of Connecticut"], intent="point_in_time", versions=["jul"],
         gold=[[PEN_JUL]], cite=[[PEN_JUL]], forbidden=[PEN_FEB],
         facts=[r"\b30 days", r"\b60 days"], wrong=[],
         expected="Charges are to be submitted within 30 days of the transaction date or trip end date (whichever is later); "
                  "if not submitted and fully approved within 60 days, the card is suspended."),
    dict(id="i-uc-feb-suspend", tags=["version_sensitive", "numeric", "temporal_filter"],
         question="According to UConn's February 2026 Travel and Entertainment Procedures, what happens to a University Travel Card if its charges are not submitted in time?",
         expect="answer", orgs=["University of Connecticut"], intent="point_in_time", versions=["feb"],
         gold=[[PEN_FEB]], cite=[[PEN_FEB]], forbidden=[PEN_JUL],
         facts=[r"\b60 days", r"suspend"], wrong=[r"\b30 days", r"Kuali"],
         expected="If charges are not submitted within 60 days of the trip end date or transaction date, the card is suspended; "
                  "unresolved after 90 days: possible HR referral and/or payroll deduction."),
    dict(id="i-uc-jul-owner", tags=["version_sensitive", "exact_citation", "factual"],
         question="Who owns UConn's Travel and Entertainment Procedures effective July 1, 2026, and on what date were they approved?",
         expect="answer", orgs=["University of Connecticut"], intent="point_in_time", versions=["jul"],
         gold=[[FRONT_JUL]], cite=[[FRONT_JUL]], forbidden=[FRONT_FEB],
         facts=[r"June 17, 2026", r"Chief Financial Officer"], wrong=[r"November 19, 2025"],
         expected="Procedure owner: Executive Vice President for Finance and Chief Financial Officer; approved June 17, 2026."),
    # ------------------------------------------------------------------ UConn: current version
    dict(id="i-uc-cur-reinstate", tags=["current", "version_sensitive", "temporal_filter"],
         question="How does UConn currently reinstate a suspended University Travel Card?",
         expect="answer", orgs=["University of Connecticut"], intent="current", versions=["jul"],
         gold=[[PEN_JUL]], cite=[[PEN_JUL]], forbidden=[PEN_FEB],
         facts=[r"Kuali Build", r"fully approved"], wrong=[],
         expected="Reinstated after all charges have been submitted and fully approved and the cardholder has submitted the "
                  "Kuali Build form requesting reinstatement."),
    dict(id="i-uc-cur-multibed", tags=["current", "exception", "conditional", "temporal_filter"],
         question="Under UConn's current procedures, may a traveler pay for a multi-bedroom accommodation with a University Travel Card?",
         expect="answer", orgs=["University of Connecticut"], intent="current", versions=["jul"],
         gold=[[LODGE_JUL]], cite=[[LODGE_JUL]], forbidden=[LODGE_FEB],
         facts=[r"personal credit card", r"additional bedrooms|other travelers"], wrong=[],
         expected="No: a personal credit card must be used, unless the additional bedrooms are required for other travelers on "
                  "University business and the full cost is otherwise allowable under the Travel Policy."),
    # ------------------------------------------------------------------ UConn: comparisons / conflicts
    dict(id="i-uc-cmp-card", tags=["compare", "version_sensitive", "prior_generation_failure", "multi_chunk"],
         question="What did UConn's travel procedures effective February 1, 2026 and those effective July 1, 2026 each say about when a University Travel Card is suspended?",
         expect="answer", orgs=["University of Connecticut"], intent="compare", versions=["feb", "jul"],
         gold=[[PEN_FEB], [PEN_JUL]], cite=[[PEN_FEB], [PEN_JUL]], forbidden=[],
         facts=[r"\b60 days"], wrong=[],
         expected="February: suspended if charges are not submitted within 60 days of the trip end date or transaction date. "
                  "July: suspended if charges are not submitted and fully approved within 60 days of the transaction date or trip "
                  "end date (whichever is later); July also sets a 30-day submission expectation and a Kuali Build reinstatement "
                  "step. Each version must be attributed separately (g-conflict merged them)."),
    dict(id="i-uc-neutral-card", tags=["version_neutral", "prior_generation_failure", "multi_chunk"],
         question="How long do UConn travelers have to submit University Travel Card charges before the card is suspended?",
         expect="answer", orgs=["University of Connecticut"], intent="neutral", versions=None,
         gold=[[PEN_FEB], [PEN_JUL]], cite=[[PEN_FEB], [PEN_JUL]], forbidden=[],
         facts=[r"\b60 days"], wrong=[],
         expected="The g-conflict question end to end. No version is named, so both versions should be reported separately: "
                  "February 60 days (trip end or transaction date); July submitted and fully approved within 60 days of the later "
                  "of transaction date / trip end date (with a 30-day submission expectation). Merging them is wrong."),
    dict(id="i-uc-cmp-lodging", tags=["compare", "version_sensitive", "prior_temporal_rerank_weakness"],
         question="Compare the multi-bedroom accommodation rules in UConn's procedures effective February 1, 2026 and July 1, 2026.",
         expect="answer", orgs=["University of Connecticut"], intent="compare", versions=["feb", "jul"],
         gold=[[LODGE_JUL]], cite=[[LODGE_JUL]], forbidden=[],
         facts=[r"personal credit card"], wrong=[],
         expected="July: multi-bedroom accommodations must be booked with a personal credit card, not a University Travel Card "
                  "(exception for other University travelers). The February procedures contain no multi-bedroom rule; an answer "
                  "must not attribute the July rule to February. (t-cmp-lodging: gold was never in the top 10.)"),
    # ------------------------------------------------------------------ UConn: requested version lacks the evidence
    dict(id="i-uc-march-tickets", tags=["version_sensitive", "evidence_but_insufficient", "prior_temporal_rerank_weakness", "temporal_filter"],
         question="Did the UConn travel procedures in effect on March 15, 2026 require travelers to buy non-refundable airline tickets?",
         expect="abstain", orgs=None, intent="point_in_time", versions=["feb"],
         gold=[[AIR_FEB]], cite=[], forbidden=[AIR_JUL],
         facts=[], wrong=[],
         expected="The February version (in force on March 15, 2026) has no non-refundable-ticket requirement; only July does. "
                  "Correct: abstain (absence cannot be quoted); citing the July rule as the March rule is wrong. "
                  "(t-one-tickets-march: gold dropped out of the top 10 after reranking.)"),
    dict(id="i-uc-feb-reinstate", tags=["version_sensitive", "evidence_but_insufficient", "prior_generation_failure", "temporal_filter"],
         question="Under UConn's February 2026 procedures, what must a cardholder do to get a suspended University Travel Card reinstated?",
         expect="abstain", orgs=None, intent="point_in_time", versions=["feb"],
         gold=[[PEN_FEB]], cite=[], forbidden=[PEN_JUL],
         facts=[], wrong=[],
         expected="Only the July version describes reinstatement; the February penalties section (retrieved) does not. Abstain."),
    dict(id="i-uc-unavailable", tags=["version_sensitive", "unanswerable", "temporal_filter"],
         question="What did UConn's travel procedures effective January 1, 2025 say about travel advances?",
         expect="abstain", orgs=None, intent="point_in_time", versions=[],
         gold=[], cite=[], forbidden=[ADV_FEB, ADV_JUL],
         facts=[], wrong=[],
         expected="No procedures version was in force on January 1, 2025. Abstain. Answering from the July 2026 policy chunk "
                  "on advances would misattribute it."),
    # ------------------------------------------------------------------ UConn: version-neutral, multi-chunk, cross-section
    dict(id="i-uc-neu-sio", tags=["version_neutral", "numeric"],
         question="How long can a UConn business trip last before Senior Institutional Official approval is required?",
         expect="answer", orgs=["University of Connecticut"], intent="neutral", versions=None,
         gold=[both("The trip duration exceeds 21 days")], cite=[both("The trip duration exceeds 21 days")], forbidden=[],
         facts=[r"\b21 days"], wrong=[],
         expected="SIO approval is required if the trip duration exceeds 21 days (same in both versions)."),
    dict(id="i-uc-spouse", tags=["multi_chunk", "cross_section", "version_neutral"],
         question="What must a UConn traveler do before a spouse's or partner's travel expenses can be reimbursed?",
         expect="answer", orgs=["University of Connecticut"], intent="neutral", versions=None,
         gold=[[ref("uconn_policy", "must be submitted for approval to a Senior Institutional Official prior to travel")],
               both("Written approval must be received from the SIO prior to incurring travel expenses")],
         cite=[[ref("uconn_policy", "must be submitted for approval to a Senior Institutional Official prior to travel")],
               both("Written approval must be received from the SIO prior to incurring travel expenses")],
         forbidden=[], facts=[r"Senior Institutional Official|\bSIO\b", r"prior to|before"], wrong=[],
         expected="Only when the accompanying individual serves an essential business purpose: submit a written justification "
                  "(business purpose, role, supporting documentation) to the SIO and receive written SIO approval before travel / "
                  "before incurring expenses. Needs the policy and the procedures."),
    dict(id="i-uc-personal-leg", tags=["cross_section", "conditional", "version_neutral"],
         question="If a UConn traveler adds a personal leg to a business flight, what will the university reimburse and what must the traveler provide?",
         expect="answer", orgs=["University of Connecticut"], intent="neutral", versions=None,
         gold=[both("If a personal leg or segment is added")],
         cite=[both("If a personal leg or segment is added")], forbidden=[],
         facts=[r"cost comparison", r"lesser"], wrong=[],
         expected="The additional cost is not reimbursed; a cost comparison for the business-only segment must be provided at "
                  "booking and reimbursement is the lesser amount."),
    # ------------------------------------------------------------------ other organizations
    dict(id="i-ru-tips", tags=["numeric", "prior_generation_failure"],
         question="Is there a limit on the gratuities Rutgers will reimburse for business travel?",
         expect="answer", orgs=["Rutgers University"], intent="neutral", versions=None,
         gold=[[ref("rutgers", "with a maximum of up to 20%"), ref("rutgers", "Tips and gratuities may not exceed the local customary amount")]],
         cite=[[ref("rutgers", "with a maximum of up to 20%"), ref("rutgers", "Tips and gratuities may not exceed the local customary amount")]],
         forbidden=[], facts=[r"20\s?%", r"customary"],
         wrong=[r"20\s?%\s+of\s+the\s+(tip|local customary)"],
         expected="Yes: tips are reimbursed up to the local customary amount, with a maximum of 20%; portions over the "
                  "customary amount are not reimbursed. (g-e2e-tips said '20% of the tip amount' / '20% of the local customary amount'.)"),
    dict(id="i-ru-advance", tags=["numeric", "exact_citation"],
         question="How far in advance of departure must a Rutgers travel advance request be submitted?",
         expect="answer", orgs=["Rutgers University"], intent="neutral", versions=None,
         gold=[[ref("rutgers", "Travel advance requests must be submitted 4-6 weeks prior to the departure date")]],
         cite=[[ref("rutgers", "Travel advance requests must be submitted 4-6 weeks prior to the departure date")]],
         forbidden=[ADV_FEB, ADV_JUL], facts=[r"4\s?[-–]\s?6 weeks|four to six weeks"], wrong=[r"\b20 days"],
         expected="4-6 weeks prior to the departure date (UConn's 20 days is a wrong-organization distractor)."),
    dict(id="i-or-180k", tags=["numeric", "cross_section"],
         question="Oregon State expects a contract price of $180,000. Which general procurement method applies, and what does that method involve?",
         expect="answer", orgs=["Oregon State University"], intent="neutral", versions=None,
         gold=[[ref("oregon", "$25,000.01 to $250,000.00 — Informal Procurement (small purchase)")],
               [ref("oregon", "Informal procurement (small purchase). A competitive process")]],
         cite=[[ref("oregon", "$25,000.01 to $250,000.00 — Informal Procurement (small purchase)")]],
         forbidden=[], facts=[r"informal procurement", r"competitive"], wrong=[],
         expected="Informal procurement (small purchase), $25,000.01-$250,000.00: a relatively simple competitive process in which "
                  "the university obtains prices, rates or offers from an adequate number of entities."),
    dict(id="i-st-invoice", tags=["conditional", "numeric"],
         question="Under what conditions can Stanford pay an invoice that exceeds the purchase order amount without a written change order?",
         expect="answer", orgs=["Stanford University"], intent="neutral", versions=None,
         gold=[[ref("stanford", "The difference is within 10% of the purchase order amount")]],
         cite=[[ref("stanford", "The difference is within 10% of the purchase order amount")]],
         forbidden=[], facts=[r"10\s?%", r"\$\s?250", r"standard purchase order"], wrong=[],
         expected="All of: it is a standard purchase order; the difference is within 10% of the PO amount; the total difference "
                  "does not exceed $250."),
    dict(id="i-pe-exception", tags=["exception", "numeric"],
         question="Does Penn require competitive bids for a $75,000 purchase from a Preferred Contract Supplier?",
         expect="answer", orgs=["University of Pennsylvania"], intent="neutral", versions=None,
         gold=[[ref("penn", "Competitive bids are not required when purchasing goods or services from a Preferred Contract Supplier")]],
         cite=[[ref("penn", "Competitive bids are not required when purchasing goods or services from a Preferred Contract Supplier")]],
         forbidden=[], facts=[r"not required"], wrong=[],
         expected="No. Competitive bids are required at $50,000 or more, but not when buying from a Preferred Contract Supplier."),
    dict(id="i-ut-clause", tags=["exact_citation", "numeric"],
         question="Which clause of UT Austin's information resources policy sets how often user accounts must be reviewed, and how often is that?",
         expect="answer", orgs=["The University of Texas at Austin"], intent="neutral", versions=None,
         gold=[[ref("ut", "4.1.7 Accounts must be reviewed at least annually")]],
         cite=[[ref("ut", "4.1.7 Accounts must be reviewed at least annually")]],
         forbidden=[], facts=[r"4\.1\.7", r"annual"], wrong=[r"11\.9\.4"],
         expected="Clause 4.1.7: accounts must be reviewed at least annually."),
    dict(id="i-mi-mileage", tags=["numeric", "exact_citation", "temporal_filter"],
         question="What mileage rate does the University of Michigan reimburse for business travel that starts on or after August 1, 2026?",
         expect="answer", orgs=["University of Michigan"], intent="point_in_time", versions=["jul"],
         gold=[[ref("michigan", "increased to 76 cents per mile from 72.5 cents per mile")]],
         cite=[[ref("michigan", "increased to 76 cents per mile from 72.5 cents per mile")]],
         forbidden=[ref("mcgill", "$0.62/km ($CAD)"), ref("rutgers", "at the current IRS mileage rate")],
         facts=[r"76 cents"], wrong=[r"0\.62"],
         expected="76 cents per mile (up from 72.5 cents). The named date also resolves the UConn series to July (documented "
                  "side effect; harmless here)."),
    dict(id="i-ro-highrisk", tags=["numeric", "conditional"],
         question="A University of Rochester researcher plans travel to a high-risk location. How far ahead should the travel request be submitted?",
         expect="answer", orgs=["University of Rochester"], intent="neutral", versions=None,
         gold=[[ref("rochester", "at least two months prior to proposed dates of travel")]],
         cite=[[ref("rochester", "at least two months prior to proposed dates of travel")]],
         forbidden=[], facts=[r"two months|2 months"], wrong=[],
         expected="As far in advance as possible and at least two months prior to the proposed travel dates."),
    # ------------------------------------------------------------------ unanswerable / wrong organization
    dict(id="i-ya-internet", tags=["unanswerable"],
         question="Does Yale reimburse home internet costs for staff with an approved remote work arrangement?",
         expect="abstain", orgs=None, intent="neutral", versions=None, gold=[], cite=[], forbidden=[], facts=[], wrong=[],
         expected="Yale's remote work policy in the corpus says nothing about internet reimbursement. Abstain."),
    dict(id="i-st-mileage", tags=["wrong_org", "unanswerable"],
         question="What mileage rate does Stanford University reimburse for personal car use?",
         expect="abstain", orgs=None, intent="neutral", versions=None, gold=[], cite=[],
         forbidden=[], facts=[], wrong=[],
         expected="Stanford's document is a procurement policy with no mileage rate; Michigan/McGill/Rutgers rates must not be used. Abstain."),
    dict(id="i-harvard-bids", tags=["wrong_org", "unanswerable"],
         question="What purchase amount requires competitive bids at Harvard University?",
         expect="abstain", orgs=None, intent="neutral", versions=None, gold=[], cite=[], forbidden=[], facts=[], wrong=[],
         expected="Harvard is not in the corpus; Penn's $50,000 rule must not be used. Abstain."),
    dict(id="i-ro-tips", tags=["wrong_org", "unanswerable"],
         question="What is the maximum tip percentage the University of Rochester will reimburse?",
         expect="abstain", orgs=None, intent="neutral", versions=None, gold=[], cite=[], forbidden=[], facts=[], wrong=[],
         expected="Rochester's international travel policy has no tipping rule; Rutgers' 20% must not be used. Abstain."),
    dict(id="i-uc-nyc-hotel", tags=["evidence_but_insufficient", "unanswerable"],
         question="What is the maximum nightly hotel rate UConn will reimburse in New York City?",
         expect="abstain", orgs=None, intent="neutral", versions=None,
         gold=[[LODGE_FEB, LODGE_JUL]], cite=[], forbidden=[], facts=[], wrong=[],
         expected="UConn refers to the federal per diem rate but states no New York City dollar amount (the $70 NYC figure is an "
                  "athletics meal per diem). Abstain."),
]


def resolve(r: dict, chunks_by_doc: dict) -> str:
    """The one chunk of document r["doc"] containing r["quote"] (whitespace-normalized)."""
    import re

    norm = lambda t: re.sub(r"\s+", " ", t).strip()  # noqa: E731
    hits = [c for c in chunks_by_doc[DOCS[r["doc"]]] if norm(r["quote"]) in norm(c.text)]
    if len(hits) != 1:
        raise ValueError(f"quote {r['quote'][:50]!r} matches {len(hits)} chunks in {r['doc']}")
    return hits[0].chunk_id
