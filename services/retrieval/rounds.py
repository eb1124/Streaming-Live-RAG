"""One span per round of the phase 6 loop (phase 10A).

  TracedIterativeRetriever(max_rounds, aliases, ids=req.ids())(stack, query, k)      # what IterativeRetriever returns, unchanged

adaptive.streaming.controller.IterativeRetriever is not changed and nothing it computes is touched: the subclass
only observes where a round ends. The loop asks `_next` for a refinement after every round that did not settle the
question, so a round's span runs from the loop's start (round 1) or from the refinement it was issued for, to that
call (or to the end of the loop). The span is a child of the current span (retrieval.execute) and carries counts
and decisions read from the round's entry in the RetrievalTrace: never the query, a chunk id or any text. The
trace itself is returned as before; it is not copied into the span.

One instance per retrieval (RetrievalService makes one per request): the open span is instance state.
"""

from __future__ import annotations

import logging

from adaptive.streaming.controller import DEFAULT_MAX_ROUNDS, IterativeRetriever
from adaptive.streaming.state import SUFFICIENT
from generation.pipeline import RETRIEVE_K

from .. import telemetry

log = logging.getLogger(__name__)


def round_attributes(r) -> dict:
    return {"retrieval.round.number": r.iteration, "retrieval.round.strategy": r.strategy,
            "retrieval.round.retrieved_count": len(r.retrieved), "retrieval.round.new_count": len(r.new),
            "retrieval.round.excluded_count": len(r.excluded_versions),
            "retrieval.round.retained_count": len(r.retained), "retrieval.round.context_count": len(r.context),
            "retrieval.round.promoted_count": len(r.promoted), "retrieval.coverage.decision": r.coverage.decision,
            "retrieval.coverage.achieved": r.coverage.decision == SUFFICIENT}


class TracedIterativeRetriever(IterativeRetriever):
    def __init__(self, max_rounds: int = DEFAULT_MAX_ROUNDS, aliases: dict[str, str] | None = None, observer=None,
                 ids: dict | None = None):
        super().__init__(max_rounds, aliases, observer)
        self._ids = ids  # the correlation ids the round spans carry (the request's)
        self._round: telemetry.Span | None = None

    def _open(self) -> None:
        self._round = telemetry.start("retrieval.round", telemetry.RETRIEVAL, ids=self._ids)

    def _close(self, trace=None, decision: str | None = None, exc: BaseException | None = None) -> None:
        span, self._round = self._round, None
        if span is None:
            return
        try:
            if trace is not None and trace.rounds:
                span.set(round_attributes(trace.rounds[-1]))
                span.set({"retrieval.round.decision": decision,
                          "retrieval.stop_reason": trace.stop_reason if decision == "stop" else None})
            if exc is not None:
                span.fail(exc)
        except Exception:
            log.debug("round span attributes failed; the retrieval is not affected", exc_info=True)
        span.end()

    def __call__(self, stack, query: str, k: int = RETRIEVE_K):
        self._open()
        try:
            r = super().__call__(stack, query, k)
        except Exception as e:
            self._close(exc=e)
            raise
        self._close(r.trace, "stop")
        return r

    def _next(self, query, cov, issued, trace):
        step = super()._next(query, cov, issued, trace)
        if step is not None:  # a refinement was issued: this round is over and the next one starts
            self._close(trace, "refine")
            self._open()
        return step
