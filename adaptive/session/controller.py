"""Session-aware controller (AdaptiveRAG phase 3), above the multi-intent controller (phase 2), unchanged.

  controller = SessionController(stack, provider)
  session = Session()
  turn = controller.ask(session, "What is UConn's University Travel Card suspension rule?")
  turn = controller.ask(session, "Does that apply currently?")

  utterance -> turn gate (adaptive.session.gate; rules, no model): is it a question for the corpus?
     wait          -> no retrieval, no model call: the unfinished text is kept; the next utterance continues it
     suppress      -> no retrieval, no model call, no text: an acknowledgement or a closing
     presentation  -> no retrieval, no model call: the last answer of the session, laid out as asked
     retrieve      -> resolve against the last question of the session (adaptive.session.resolve; rules, no model)
        self_contained -> MultiIntentController.run(question)          exactly phase 2
        follow_up      -> MultiIntentController.run(rewritten query)   phase 2 on one standalone query
        refinement     -> a late constraint on the answered question: one targeted retrieval for the delta, one
                          answer from it, merged with the stored answer (adaptive.session.refine): answer v2
        unresolved     -> abstain: no retrieval, no model call
  -> Turn appended to the session

Every answered turn retrieves, answers and verifies its own evidence; its citations are its own. A presentation
turn is the exception by design: its answer is the earlier turn's (claims, citations, evidence), with only its text
laid out again. A waiting, suppressed or presentation turn does not change what a later follow-up is resolved
against (Session.context). Nothing here changes retrieval, temporal resolution, the prompt, the verifier, phase 1
or phase 2.
"""

from __future__ import annotations

from adaptive.multi.controller import MultiIntentController
from generation.pipeline import RetrievalStack
from generation.providers import LLMProvider

from .gate import RETRIEVE, decide, join, present
from .refine import REFINED, merge
from .resolve import resolve
from .state import PRESENTATION, REFINEMENT, SUPPRESS, UNRESOLVED, WAIT, Resolution, Session, Turn

REFINE = "refine"  # signals["decision"] of a refinement turn (the gate's own decision for it is "retrieve")


class SessionController:
    def __init__(self, stack: RetrievalStack, provider: LLMProvider, aliases: dict[str, str] | None = None,
                 retriever=None, answerer=None):
        # retriever: None = the frozen retrieval; phase 6 passes its iterative retriever (current turn only)
        # answerer: None = the frozen GroundedAnswerer; phase 7 passes the generation service's client
        self.multi = MultiIntentController(stack, provider, aliases, retriever, answerer)
        self._chunks: dict | None = None  # the corpus's chunks by id (a refinement reads its earlier evidence there)

    def ask(self, session: Session, question: str) -> Turn:
        waiting = session.last if session.last and session.last.resolution.kind == WAIT else None
        context = session.context  # the last turn that was a question
        # what the user has said so far: an unfinished utterance is continued by this one
        utterance = join(waiting.resolution.signals["pending"], question) if waiting else question
        d = decide(question)
        if not (waiting and d.kind == SUPPRESS):  # "never mind, thanks" after an unfinished question closes it
            d = decide(utterance)
        signals = {"decision": d.kind, **d.signals}
        if waiting:
            signals["continues_turn"] = waiting.index
        anchor = context.index if context else None
        answer = None

        if d.kind == WAIT:
            r = Resolution(WAIT, d.reason, "", anchor=anchor, signals=signals | {"pending": utterance})
        elif d.kind == SUPPRESS:
            r = Resolution(SUPPRESS, d.reason, "", anchor=anchor, signals=signals)
        elif d.kind == PRESENTATION:
            if context is None or context.answer is None:
                r = Resolution(UNRESOLVED, f"{d.reason}, but this session has no answer to lay out", "",
                               anchor=anchor, signals=signals)
            else:
                answer = present(context.answer, d.signals["layout"])
                c = context.resolution
                r = Resolution(PRESENTATION, d.reason, "", list(c.topic), c.temporal, list(c.organizations), anchor, signals)
        else:
            assert d.kind == RETRIEVE
            r = resolve(utterance, context, self.multi.organizations, self.multi.aliases)
            r.signals = {**r.signals, **signals, "retrieval_required": r.kind != UNRESOLVED}
            if r.kind == REFINEMENT:
                r.signals["decision"] = REFINE
                answer, r.signals["refinement"] = self._refine(context, r)
            elif r.kind != UNRESOLVED:
                answer = self.multi.run(r.query)

        # a question completed over several utterances is recorded whole: later follow-ups are read with it
        turn = Turn(len(session.turns), utterance if d.kind == RETRIEVE else question, r, answer)
        if answer is not None and r.anchor is not None:
            earlier = set(session.turns[r.anchor].cited_chunks)
            turn.reused_citations = [c for c in turn.cited_chunks if c in earlier]
        session.turns.append(turn)
        return turn

    def _refine(self, earlier: Turn, r: Resolution):
        """Answer v2 of the question `earlier` answered, under the late constraint `r` resolved (refine, do not
        restart): one retrieval, for the delta only (r.signals["retrieval_query"]: the constraint's new words and
        the topic they apply to); one phase 1 answer to the question under the constraint (r.query), from that
        evidence; then the stored answer v1 and that answer are merged (adaptive.session.refine). `earlier` is read,
        not changed. Returns (answer v2, the record of what was kept, replaced, dropped and added)."""
        multi = self.multi
        targeted = multi.aligned(multi.stack, r.signals["retrieval_query"])
        reason = f"refinement of turn {earlier.index} by a late constraint: targeted retrieval for {r.signals['delta']!r}"
        delta = multi.answer(r.query, targeted, REFINED, reason)
        if self._chunks is None:
            self._chunks = {c.chunk_id: c for c in multi.stack.chunks}
        return merge(earlier.answer, delta, self._chunks, r.signals["constraint"])
