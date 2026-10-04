"""Generation / evidence test suite (separate from the frozen retrieval benchmark, which is not used here).

This is a behavioral check of grounding, citation and abstention on a handful of cases, not a
benchmark of answer quality. Each case either supplies a fixed context (chunks resolved from verbatim
quotes, in the order given, distractors included) or runs the real retrieval pipeline ("retrieve").

Checks per case (all automatic, see run.py):
  status        expected outcome: "answered" or "abstained"
  must_cite     every listed chunk must be among the answer's citations
  must_cite_any at least one chunk of each group must be cited
  must_not_cite none of these chunks may be cited
  must_contain  substrings the answer text must contain (case-insensitive)
Always, for answered cases: the answer passed verification (every claim cited, quotes found in the
cited sources, numbers present in them) and every citation points to a chunk that was in the context.
"""

from evaluation.retrieval.cases import DOCS


def ref(doc: str, quote: str) -> dict:
    return {"doc": doc, "quote": quote}


CASES = [
    dict(id="g-direct", category="direct_fact",
         question="Which system must UConn workforce members and students use to submit a Travel Request?",
         context=[ref("uconn_jul", "must submit a Travel Request form through the Concur"),
                  ref("rochester", "no less than 30 days prior to departure"),
                  ref("uconn_policy", "Travel requiring airfare or lodging must be pre-approved")],
         status="answered", must_cite=[ref("uconn_jul", "must submit a Travel Request form through the Concur")],
         must_contain=["Concur"]),
    dict(id="g-numeric", category="numeric_threshold",
         question="What is the dollar threshold for a direct procurement (micro-purchase) at Oregon State University?",
         context=[ref("oregon", "$250,000.00 or less — Targeted Reserve Contracting"),
                  ref("oregon", "$25,000.00 or less — Direct Procurement")],
         status="answered", must_cite=[ref("oregon", "$25,000.00 or less — Direct Procurement")],
         must_not_cite=[ref("oregon", "$250,000.00 or less — Targeted Reserve Contracting")],
         must_contain=["25,000"]),
    dict(id="g-conditional", category="conditional",
         question="When is a Rutgers traveler considered to be in travel status for meal reimbursement?",
         context=[ref("rutgers", "Trips less than 100 miles one way do not qualify"),
                  ref("rutgers", "For a period of at least 12 consecutive hours")],
         status="answered", must_cite=[ref("rutgers", "For a period of at least 12 consecutive hours")],
         must_contain=["12"]),
    dict(id="g-exception", category="exception",
         question="Does Yale's remote work policy apply to staff in bargaining unit positions?",
         context=[ref("yale", "A work location outside the State of Connecticut"),
                  ref("yale", "Staff members working in bargaining unit positions are not covered by this policy")],
         status="answered",
         must_cite=[ref("yale", "Staff members working in bargaining unit positions are not covered by this policy")],
         must_contain=["bargaining"]),
    dict(id="g-citation", category="citation_correctness",
         question="What mileage rate does McGill University reimburse for using a personal car?",
         context=[ref("michigan", "increased to 76 cents per mile"),
                  ref("mcgill", "$0.62/km ($CAD)"),
                  ref("rutgers", "at the current IRS mileage rate")],
         status="answered", must_cite=[ref("mcgill", "$0.62/km ($CAD)")],
         must_not_cite=[ref("michigan", "increased to 76 cents per mile"), ref("rutgers", "at the current IRS mileage rate")],
         must_contain=["0.62"]),
    dict(id="g-multichunk", category="multi_chunk",
         question="What approvals and documentation does UConn require before a spouse's travel expenses can be reimbursed?",
         context=[ref("uconn_policy", "must be submitted for approval to a Senior Institutional Official prior to travel"),
                  ref("uconn_jul", "Written approval must be received from the SIO prior to incurring travel expenses")],
         status="answered",
         must_cite=[ref("uconn_policy", "must be submitted for approval to a Senior Institutional Official prior to travel"),
                    ref("uconn_jul", "Written approval must be received from the SIO prior to incurring travel expenses")]),
    dict(id="g-insufficient", category="insufficient_evidence",
         question="Does the University of Michigan reimburse pet boarding costs during business travel?",
         context=[ref("michigan", "Employees are strongly encouraged to book airfare"),
                  ref("michigan", "reimburse lodging expenses at a reasonable standard room rate")],
         status="abstained"),
    dict(id="g-wrong-org", category="insufficient_evidence",
         question="What is Oregon State University's threshold for formal procurement?",
         context=[ref("rutgers", "Tickets should be purchased through the university travel agency"),
                  ref("rutgers", "Employees are required to use Concur Online Travel")],
         status="abstained"),
    dict(id="g-conflict", category="conflicting_versions",
         question="How long do UConn travelers have to submit University Travel Card charges before the card is suspended?",
         context=[ref("uconn_feb", "submitted within 60 days of the trip end date or transaction date, the card will be suspended"),
                  ref("uconn_jul", "the cardholder has submitted the Kuali Build form requesting reinstatement")],
         status="answered",
         must_cite=[ref("uconn_feb", "submitted within 60 days of the trip end date or transaction date, the card will be suspended"),
                    ref("uconn_jul", "the cardholder has submitted the Kuali Build form requesting reinstatement")],
         must_contain=["60"],
         notes="The question names no version; the two versions state the rule differently. Expected: both "
               "cited, not merged."),
    dict(id="g-version-july", category="uconn_version",
         question="Under UConn's travel procedures effective July 1, 2026, can a University Travel Card be used to pay for a multi-bedroom accommodation?",
         context=[ref("uconn_feb", "Lodging costs may exceed the federal per diem rate by more than 50%"),
                  ref("uconn_jul", "Travelers booking a multi-bedroom accommodation must use a personal credit card")],
         status="answered",
         must_cite=[ref("uconn_jul", "Travelers booking a multi-bedroom accommodation must use a personal credit card")],
         must_not_cite=[ref("uconn_feb", "Lodging costs may exceed the federal per diem rate by more than 50%")],
         must_contain=["personal credit card"]),
    dict(id="g-version-feb-trap", category="uconn_version",
         question="Under the UConn procedures effective February 1, 2026, how is a suspended University Travel Card reinstated?",
         context=[ref("uconn_feb", "submitted within 60 days of the trip end date or transaction date, the card will be suspended"),
                  ref("uconn_jul", "the cardholder has submitted the Kuali Build form requesting reinstatement")],
         status="abstained",
         notes="Only the July version describes reinstatement. Answering from the July chunk would misattribute "
               "a July rule to the February version, so the correct behavior is to abstain."),
    dict(id="g-e2e-tips", category="end_to_end",
         question="What is the maximum tip percentage Rutgers will reimburse?",
         context="retrieve", status="answered",
         must_cite=[ref("rutgers", "with a maximum of up to 20%")], must_contain=["20%"]),
    dict(id="g-e2e-accounts", category="end_to_end",
         question="How often must UT Austin user accounts be reviewed?",
         context="retrieve", status="answered",
         must_cite=[ref("ut", "4.1.7 Accounts must be reviewed at least annually")], must_contain=["annual"]),
]


def resolve(r: dict, chunks_by_doc: dict) -> str:
    """The one chunk of document r["doc"] containing r["quote"] (whitespace-normalized)."""
    import re

    norm = lambda t: re.sub(r"\s+", " ", t).strip()  # noqa: E731
    hits = [c for c in chunks_by_doc[DOCS[r["doc"]]] if norm(r["quote"]) in norm(c.text)]
    if len(hits) != 1:
        raise ValueError(f"quote {r['quote'][:50]!r} matches {len(hits)} chunks in {r['doc']}")
    return hits[0].chunk_id
