"""Phase 3 (session-aware refinement) conversations. Separate from the integration and phase 2 suites; reuses their
evidence conventions (evaluation/integration/cases.py: ref()).

Conversation fields:
  id, tags   what the conversation exercises
  turns      in order; each turn:
               question  verbatim
               kind      expected resolution: self_contained | follow_up | unresolved
               gold      evidence the model must be shown for this turn; each unit is a list of acceptable refs
                         (any chunk of the document containing the quote suffices)
               temporal  expected temporal intent of the query phase 2 receives (optional)
               version   document key whose version must be the selected one (optional)
Gold was written from the source text before any phase 3 run.
"""

from evaluation.integration.cases import ref


def turn(question, kind, gold=(), temporal=None, version=None):
    return {"question": question, "kind": kind, "gold": [list(u) for u in gold], "temporal": temporal,
            "version": version}


RU_ADVANCE = ref("rutgers", "Travel advance requests must be submitted 4-6 weeks prior to the depar")
RU_AFTER = ref("rutgers", "responsible for submitting their expenses in Concur immediately upon return from travel")
PEN_FEB = ref("uconn_feb", "submitted within 60 days of the trip end date or transaction date, the card will be suspended")
PEN_JUL = ref("uconn_jul", "not submitted and fully approved within 60 days of the transaction date or the trip end date")
PEN_90 = [ref("uconn_feb", "If charges remain unresolved after 90 days"),
          ref("uconn_jul", "If charges remain unresolved after 90 days")]
OR_INFORMAL = ref("oregon", "$25,000.01 to $250,000.00 — Informal Procurement (small purchase)")
OR_EMERGENCY = ref("oregon", "with the approval of the Assistant Vice President and Chief Procurement Officer if the "
                             "anticipated costs are less than $2,000,000")
UT_REVIEW = ref("ut", "4.1.7 Accounts must be reviewed at least annually")
Q_UC_CARD = "What is UConn's University Travel Card suspension rule?"
Q_RU_ADV = "What are Rutgers' rules for travel advances?"

CONVERSATIONS = [
    dict(id="s-direct", tags=["direct_follow_up", "new_retrieval"], turns=[
        turn(Q_RU_ADV, "self_contained", [[RU_ADVANCE]]),
        turn("What documentation is required after the trip?", "follow_up", [[RU_AFTER]])]),
    dict(id="s-reference", tags=["reference"], turns=[
        turn(Q_UC_CARD, "self_contained", [[PEN_FEB, PEN_JUL]]),
        turn("What happens if they are still unresolved after 90 days?", "follow_up", [PEN_90])]),
    dict(id="s-temporal", tags=["temporal_refinement", "version_change"], turns=[
        turn("What did UConn's February 2026 procedures say about when a University Travel Card is suspended?",
             "self_contained", [[PEN_FEB]], temporal="point_in_time", version="uconn_feb"),
        turn("Does that still apply under the current procedures?", "follow_up", [[PEN_JUL]], temporal="current",
             version="uconn_jul")]),
    dict(id="s-chain", tags=["temporal_inherited", "three_turns"], turns=[
        turn(Q_UC_CARD, "self_contained", [[PEN_FEB, PEN_JUL]], temporal="neutral"),
        turn("Does that apply currently?", "follow_up", [[PEN_JUL]], temporal="current", version="uconn_jul"),
        turn("What happens after 90 days?", "follow_up", [[PEN_90[1]]], temporal="current", version="uconn_jul")]),
    dict(id="s-constraint", tags=["new_constraint", "new_retrieval"], turns=[
        turn("At Oregon State, what procurement method applies to a $180,000 purchase?", "self_contained",
             [[OR_INFORMAL]]),
        turn("What if it is an emergency costing less than $2 million?", "follow_up", [[OR_EMERGENCY]])]),
    dict(id="s-unrelated", tags=["self_contained"], turns=[
        turn(Q_UC_CARD, "self_contained", [[PEN_FEB, PEN_JUL]]),
        turn("How often must UT Austin user accounts be reviewed?", "self_contained", [[UT_REVIEW]])]),
    dict(id="s-ambiguous", tags=["ambiguous_reference"], turns=[
        turn("How often must UT Austin user accounts be reviewed, and is there a limit on the gratuities Rutgers will "
             "reimburse for business travel?", "self_contained"),
        turn("Does that apply currently?", "unresolved")]),
    dict(id="s-unsupported", tags=["unsupported_follow_up", "other_organization"], turns=[
        turn(Q_RU_ADV, "self_contained", [[RU_ADVANCE]]),
        turn("Does Harvard University have the same rule?", "unresolved")]),
]
