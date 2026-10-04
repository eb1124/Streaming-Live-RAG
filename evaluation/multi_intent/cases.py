"""Phase 2 (multi-intent decomposition + evidence fusion) cases. The 27 integration cases are not changed; this
suite is separate and only reuses their conventions (evaluation/integration/cases.py: ref(), resolve()).

Case fields:
  question  the query, verbatim
  tags      what the case exercises
  multi     expected decomposition: True (several independent intents) or False (a control: phase 1 unchanged)
  intents   for multi cases, one entry per expected intent, in question order:
              expect  "answer" (the corpus answers this part) | "abstain" (it does not: the intent must not be answered)
              gold    evidence units for that intent; each unit is a list of acceptable chunks (any one suffices)
              facts   regexes a correct answer to that part matches (for a later live run; manual review otherwise)
  strategy  the strategy the case must take when it is determined by construction ("per_intent" when the intents
            select different versions of a document series), else None (decided by the coverage gate on the live
            corpus and reported, not asserted)
Gold chunks and facts were written from the source text before any phase 2 run.
"""

from evaluation.integration.cases import ref

PEN_FEB = ref("uconn_feb", "submitted within 60 days of the trip end date or transaction date, the card will be suspended")
PEN_JUL = ref("uconn_jul", "the cardholder has submitted the Kuali Build form requesting reinstatement")
LODGE_JUL = ref("uconn_jul", "Travelers booking a multi-bedroom accommodation must use a personal credit card")
SIO_JUL = ref("uconn_jul", "Written approval must be received from the SIO prior to incurring trav")
PENN_REVIEW = ref("penn", "All University purchase orders equal to or greater than $50,000 are subject to Procurement Services final review")
PENN_PCS = ref("penn", "Competitive bids are not required when purchasing goods or services from a Preferred Contract Supplier")
MI_COACH = ref("michigan", "The university will pay for the cost of standard coach airfare")
MI_14 = ref("michigan", "book airfare at least 14 days in advance")
MI_MILES = ref("michigan", "increased to 76 cents per mile from 72.5 cents per mile")
UT_REVIEW = ref("ut", "4.1.7 Accounts must be reviewed at least annually")
RU_TIPS = [ref("rutgers", "with a maximum of up to 20%"), ref("rutgers", "Tips and gratuities may not exceed the local customary amount")]
RU_ADVANCE = ref("rutgers", "Travel advance requests must be submitted 4-6 weeks prior to the depar")
ST_INVOICE = ref("stanford", "The difference is within 10% of the purchase order amount")
RO_HIGHRISK = ref("rochester", "at least two months prior to proposed dates of travel")
OR_INFORMAL = ref("oregon", "$25,000.01 to $250,000.00 — Informal Procurement (small purchase)")
OR_EMERGENCY = ref("oregon", "with the approval of the Assistant Vice President and Chief Procurement Officer if the anticipated costs are less than $2,000,000")


def part(expect, gold, facts=()):
    return {"expect": expect, "gold": gold, "facts": list(facts)}


CASES = [
    # ------------------------------------------------------------------ one organization, two needs
    dict(id="mi-uc-card", tags=["same_org", "neutral", "phase2_example"], multi=True, strategy=None,
         question="What is the UConn University Travel Card submission deadline, and what happens if charges aren't "
                  "submitted on time?",
         intents=[part("answer", [[PEN_JUL, PEN_FEB]], [r"\b(30|60) days"]),
                  part("answer", [[PEN_JUL, PEN_FEB]], [r"suspend"])]),
    dict(id="mi-penn", tags=["same_org", "scope", "different_chunks"], multi=True, strategy=None,
         question="At Penn, what happens to purchase orders of $50,000 or more before issuance, and when are "
                  "competitive bids not required?",
         intents=[part("answer", [[PENN_REVIEW]], [r"Procurement Services", r"review"]),
                  part("answer", [[PENN_PCS]], [r"Preferred Contract Supplier"])]),
    dict(id="mi-mi-air", tags=["same_org", "same_chunk"], multi=True, strategy=None,
         question="What airfare does the University of Michigan pay for, and how far in advance does the University "
                  "of Michigan recommend booking airfare?",
         intents=[part("answer", [[MI_COACH]], [r"coach"]), part("answer", [[MI_14]], [r"14 days"])]),
    dict(id="mi-or-emergency", tags=["same_org", "scope", "different_chunks", "numeric"], multi=True, strategy=None,
         question="At Oregon State, what procurement method applies to a $180,000 purchase, and what happens to the "
                  "required process if the procurement is an emergency costing less than $2 million?",
         intents=[part("answer", [[OR_INFORMAL]], [r"informal procurement"]),
                  part("answer", [[OR_EMERGENCY]], [r"Chief Procurement Officer", r"(need not|not).{0,40}competitive"])]),
    # ------------------------------------------------------------------ two organizations
    dict(id="mi-ut-ru", tags=["cross_org"], multi=True, strategy=None,
         question="How often must UT Austin user accounts be reviewed, and is there a limit on the gratuities Rutgers "
                  "will reimburse for business travel?",
         intents=[part("answer", [[UT_REVIEW]], [r"annual"]), part("answer", [RU_TIPS], [r"20\s?%|customary"])]),
    dict(id="mi-st-ro", tags=["cross_org"], multi=True, strategy=None,
         question="Under what conditions can Stanford pay an invoice that exceeds the purchase order amount without a "
                  "written change order, and how far ahead should a University of Rochester traveler submit a request "
                  "for travel to a high-risk location?",
         intents=[part("answer", [[ST_INVOICE]], [r"10\s?%", r"\$\s?250"]),
                  part("answer", [[RO_HIGHRISK]], [r"two months|2 months"])]),
    dict(id="mi-mi-ru-dated", tags=["cross_org", "date_in_one_clause"], multi=True, strategy=None,
         question="What mileage rate does the University of Michigan reimburse for business travel that starts on or "
                  "after August 1, 2026, and how far in advance of departure must a Rutgers travel advance request be "
                  "submitted?",
         intents=[part("answer", [[MI_MILES]], [r"76 cents"]),
                  part("answer", [[RU_ADVANCE]], [r"4\s?[-–]\s?6 weeks|four to six weeks"])]),
    dict(id="mi-three", tags=["cross_org", "three_intents"], multi=True, strategy=None,
         question="How often must UT Austin user accounts be reviewed, what mileage rate does the University of "
                  "Michigan reimburse for travel starting on or after August 1, 2026, and how far in advance must a "
                  "Rutgers travel advance request be submitted?",
         intents=[part("answer", [[UT_REVIEW]], [r"annual"]), part("answer", [[MI_MILES]], [r"76 cents"]),
                  part("answer", [[RU_ADVANCE]], [r"4\s?[-–]\s?6 weeks|four to six weeks"])]),
    # ------------------------------------------------------------------ versions per intent
    dict(id="mi-uc-mixed-temporal", tags=["version_per_intent", "point_in_time", "current"], multi=True,
         strategy="per_intent",
         question="What did UConn's February 2026 procedures say about when a University Travel Card is suspended, and "
                  "may a traveler currently pay for a multi-bedroom accommodation with a University Travel Card?",
         intents=[part("answer", [[PEN_FEB]], [r"\b60 days", r"suspend"]),
                  part("answer", [[LODGE_JUL]], [r"personal credit card"])]),
    dict(id="mi-uc-jul-scope", tags=["version_scope", "point_in_time"], multi=True, strategy=None,
         question="Under UConn's procedures effective July 1, 2026, how is a suspended University Travel Card "
                  "reinstated, and what approval is needed before a spouse's travel expenses can be reimbursed?",
         intents=[part("answer", [[PEN_JUL]], [r"Kuali Build"]),
                  part("answer", [[SIO_JUL]], [r"Senior Institutional Official|\bSIO\b"])]),
    # ------------------------------------------------------------------ one intent the corpus cannot answer
    dict(id="mi-ro-harvard", tags=["cross_org", "unsupported_intent"], multi=True, strategy=None,
         question="How far ahead should a University of Rochester traveler submit a request for travel to a high-risk "
                  "location, and what purchase amount requires competitive bids at Harvard University?",
         intents=[part("answer", [[RO_HIGHRISK]], [r"two months|2 months"]), part("abstain", [])]),
    dict(id="mi-ru-yale", tags=["cross_org", "unsupported_intent"], multi=True, strategy=None,
         question="How far in advance of departure must a Rutgers travel advance request be submitted, and does Yale "
                  "reimburse home internet costs for staff who work remotely?",
         intents=[part("answer", [[RU_ADVANCE]], [r"4\s?[-–]\s?6 weeks|four to six weeks"]), part("abstain", [])]),
    # ------------------------------------------------------------------ controls: must stay single (phase 1)
    dict(id="mi-ctl-single", tags=["control"], multi=False, strategy="delegate", intents=[],
         question="What does the University of Rochester policy say about tipping reimbursement?"),
    dict(id="mi-ctl-explain", tags=["control", "trailing_sentence"], multi=False, strategy="delegate", intents=[],
         question="At Stanford, can a $240 invoice overage on a $2,000 standard purchase order be paid without a "
                  "written change order? Explain why."),
    dict(id="mi-ctl-refers", tags=["control", "refers_back"], multi=False, strategy="delegate", intents=[],
         question="Which clause of UT Austin's information resources policy sets how often user accounts must be "
                  "reviewed, and how often is that?"),
    dict(id="mi-ctl-compare", tags=["control", "compare"], multi=False, strategy="delegate", intents=[],
         question="Compare the February 1, 2026 and July 1, 2026 UConn Travel Card penalty rules."),
]
