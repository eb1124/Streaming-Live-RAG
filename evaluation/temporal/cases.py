"""Temporal / version evaluation suite (UConn Travel and Entertainment Procedures, Feb vs July 2026).

Separate from the frozen retrieval benchmark (evaluation/retrieval), which is not used or changed here.
Each case: query, category, expected temporal intent, expected selected version(s) ("feb", "jul", both, or
none for neutral/unavailable), gold evidence units (each unit: any of its chunks), and "other version"
chunks that must not remain in the top 10 after resolution. Chunks are resolved from verbatim quotes.
"""

from evaluation.retrieval.cases import DOCS

SERIES = "uconn-travel-entertainment-procedures"
VERSION_DOC = {"feb": DOCS["uconn_feb"], "jul": DOCS["uconn_jul"]}


def ref(v: str, quote: str) -> dict:
    return {"doc": "uconn_feb" if v == "feb" else "uconn_jul", "quote": quote}


PEN_FEB = ref("feb", "submitted within 60 days of the trip end date or transaction date, the card will be suspended")
PEN_JUL = ref("jul", "the cardholder has submitted the Kuali Build form requesting reinstatement")
LODGE_FEB = ref("feb", "Lodging costs may exceed the federal per diem rate by more than 50%")
LODGE_JUL = ref("jul", "Travelers booking a multi-bedroom accommodation must use a personal credit card")
FRONT_FEB = ref("feb", "Approval Date: November 19, 2025")
FRONT_JUL = ref("jul", "Approval Date: June 17, 2026")
AIR_FEB = ref("feb", "If a personal leg or segment is added")
AIR_JUL = ref("jul", "Travelers must purchase non-refundable tickets")
CARS_FEB = ref("feb", "Two persons or fewer: economy/compact")
CARS_JUL = ref("jul", "Two persons or fewer: economy/compact")
ATH_FEB = [ref("feb", "d. TOTAL - $70/day"), ref("feb", "| $ 16.00 | $ 19.00 | $35.00 | $70.00 | High-Cost Cities")]
ATH_JUL = [ref("jul", "d. TOTAL - $70/day"), ref("jul", "| $ 16.00 | $ 19.00 | $35.00 | $70.00 | High-Cost Cities")]

CASES = [
    # ---- explicit July 2026
    dict(id="t-jul-lodging", category="explicit_july", intent="point_in_time", versions=["jul"],
         query="Under UConn's travel procedures effective July 1, 2026, can a University Travel Card be used to pay for a multi-bedroom accommodation?",
         gold=[[LODGE_JUL]], other=[LODGE_FEB]),
    dict(id="t-jul-card-deadline", category="explicit_july", intent="point_in_time", versions=["jul"],
         query="According to the July 2026 UConn procedures, within how many days must University Travel Card charges be submitted?",
         gold=[[PEN_JUL]], other=[PEN_FEB]),
    dict(id="t-jul-approval", category="explicit_july", intent="point_in_time", versions=["jul"],
         query="When were the UConn Travel and Entertainment Procedures effective July 1, 2026 approved?",
         gold=[[FRONT_JUL]], other=[FRONT_FEB]),
    dict(id="t-jul-asof", category="explicit_july", intent="point_in_time", versions=["jul"],
         query="Which UConn University Travel Card submission deadline applied on August 15, 2026?",
         gold=[[PEN_JUL]], other=[PEN_FEB]),
    # ---- explicit February 2026
    dict(id="t-feb-suspension", category="explicit_february", intent="point_in_time", versions=["feb"],
         query="Under the UConn procedures effective February 1, 2026, when is a University Travel Card suspended?",
         gold=[[PEN_FEB]], other=[PEN_JUL]),
    dict(id="t-feb-approval", category="explicit_february", intent="point_in_time", versions=["feb"],
         query="When were the UConn Travel and Entertainment Procedures that took effect on February 1, 2026 approved?",
         gold=[[FRONT_FEB]], other=[FRONT_JUL]),
    dict(id="t-feb-cars", category="explicit_february", intent="point_in_time", versions=["feb"],
         query="In the February 2026 UConn procedures, which rental car classes are allowed?",
         gold=[[CARS_FEB]], other=[CARS_JUL]),
    # ---- current version
    dict(id="t-cur-suspension", category="current", intent="current", versions=["jul"],
         query="What is UConn's current rule for when a University Travel Card is suspended?",
         gold=[[PEN_JUL]], other=[PEN_FEB]),
    dict(id="t-cur-tickets", category="current", intent="current", versions=["jul"],
         query="Under UConn's current travel procedures, must travelers buy non-refundable airline tickets?",
         gold=[[AIR_JUL]], other=[AIR_FEB]),
    dict(id="t-cur-athletics", category="current", intent="current", versions=["jul"],
         query="What is the current UConn athletics meal per diem for high-cost cities?",
         gold=[ATH_JUL], other=ATH_FEB),
    # ---- version-neutral (must be left unchanged)
    dict(id="t-neu-sio", category="neutral", intent="neutral", versions=None,
         query="Above what total trip cost does UConn travel need Senior Institutional Official approval?",
         gold=[[ref("feb", "The total trip cost exceeds $7,500"), ref("jul", "The total trip cost exceeds $7,500")]], other=[]),
    dict(id="t-neu-advance", category="neutral", intent="neutral", versions=None,
         query="How many days before departure must a UConn travel advance request be submitted?",
         gold=[[ref("feb", "submit it at least 20 days prior to departure"), ref("jul", "submit it at least 20 days prior to departure")]], other=[]),
    dict(id="t-neu-oregon", category="neutral", intent="neutral", versions=None,
         query="What is the dollar threshold for a direct procurement (micro-purchase) at Oregon State University?",
         gold=[[{"doc": "oregon", "quote": "$25,000.00 or less — Direct Procurement"}]], other=[]),
    # ---- conflicts between versions (both must stay)
    dict(id="t-cmp-suspension", category="conflict", intent="compare", versions=["feb", "jul"],
         query="How did UConn's University Travel Card suspension rule change between the February 2026 and July 2026 procedures?",
         gold=[[PEN_FEB], [PEN_JUL]], other=[]),
    dict(id="t-cmp-lodging", category="conflict", intent="compare", versions=["feb", "jul"],
         query="Compare the UConn lodging rules in the procedures effective February 1, 2026 and July 1, 2026.",
         gold=[[LODGE_FEB], [LODGE_JUL]], other=[]),
    # ---- only one version contains the requested evidence
    dict(id="t-one-reinstate-feb", category="one_version_only", intent="point_in_time", versions=["feb"],
         query="Under the UConn procedures effective February 1, 2026, how is a suspended University Travel Card reinstated?",
         gold=[[PEN_FEB]], other=[PEN_JUL],
         notes="Only July describes reinstatement; the July chunk must not be offered as the February rule."),
    dict(id="t-one-tickets-march", category="one_version_only", intent="point_in_time", versions=["feb"],
         query="Did the UConn travel procedures in effect on March 15, 2026 require travelers to buy non-refundable tickets?",
         gold=[[AIR_FEB]], other=[AIR_JUL],
         notes="The non-refundable requirement exists only in July; March 15, 2026 falls under the February version."),
    dict(id="t-one-unavailable", category="one_version_only", intent="point_in_time", versions=[],
         query="What did the UConn travel procedures effective January 1, 2025 say about travel advances?",
         gold=[], other=[ref("feb", "submit it at least 20 days prior to departure"), ref("jul", "submit it at least 20 days prior to departure")],
         notes="No version of the series was in force; no UConn procedures chunk should be presented."),
]


def resolve_ref(r: dict, chunks_by_doc: dict) -> str:
    import re

    norm = lambda t: re.sub(r"\s+", " ", t).strip()  # noqa: E731
    hits = [c for c in chunks_by_doc[DOCS[r["doc"]]] if norm(r["quote"]) in norm(c.text)]
    if len(hits) != 1:
        raise ValueError(f"quote {r['quote'][:50]!r} matches {len(hits)} chunks in {r['doc']}")
    return hits[0].chunk_id
