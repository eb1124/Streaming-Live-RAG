"""Phase 6 cases: questions the existing suites do not contain, where one retrieval of the whole question can miss an
organization it names. Both come from the phase 2 manual review (2026-09-30), where they stayed single-intent because
the second clause opens with a scope phrase ("..., and at Rutgers, what ..."), which decomposition did not split
then (it does since the institution-led clause rule was added to adaptive/multi/decompose.py; the recorded results
of this suite are from before it).
They were run through the loop while it was being built, so they are a demonstration of the gap it closes, not a
blind test. Fields as in evaluation/session/cases.py: gold is a list of units, each a list of acceptable refs.
"""

from evaluation.integration.cases import ref

PEN = [ref("uconn_feb", "submitted within 60 days of the trip end date or transaction date, the card will be suspended"),
       ref("uconn_jul", "not submitted and fully approved within 60 days of the transaction date or the trip end date")]
RU_ADVANCE = [ref("rutgers", "Travel advance requests must be submitted 4-6 weeks prior to the depar")]

CASES = [
    dict(id="s6-uc-ru-scope", expect="answer", orgs=["University of Connecticut", "Rutgers University"],
         question="At UConn, what is the University Travel Card suspension rule, and at Rutgers, what is the travel "
                  "advance rule?", gold=[PEN, RU_ADVANCE]),
    dict(id="s6-harvard-penn-scope", expect="partial", orgs=["University of Pennsylvania"],
         question="At Harvard University, what is the procurement policy, and at Penn, what is the procurement policy?",
         gold=[]),
]
