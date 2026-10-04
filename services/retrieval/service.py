"""Retrieval service logic (phase 7): RetrievalRequest -> RetrievalResponse, over the frozen stack.

  mode "single"     generation.pipeline.retrieve: dense + BM25 -> RRF -> temporal resolution -> rerank -> top k
  mode "iterative"  adaptive.streaming.controller.IterativeRetriever (phase 6), max_rounds from the request

Phase 10A: a retrieval.execute span around either, and a retrieval.round span per round of the loop (rounds.py).

Nothing is re-implemented: the service holds the loaded RetrievalStack (indexes and models, loaded once) and calls
those two functions. It is the only process that loads them.
"""

from __future__ import annotations

import logging

from generation.pipeline import RetrievalStack, retrieve

from .. import telemetry
from ..contracts import RetrievalRequest, RetrievalResponse
from .rounds import TracedIterativeRetriever

log = logging.getLogger(__name__)


class RetrievalService:
    def __init__(self, stack: RetrievalStack, aliases: dict[str, str] | None = None):
        self.stack, self.aliases = stack, aliases

    def handle(self, req: RetrievalRequest) -> RetrievalResponse:
        iterative = req.mode == "iterative"
        telemetry.annotate(ids=req.ids())  # the enclosing span: the HTTP request's, or the MCP tool's
        with telemetry.span("retrieval.execute", telemetry.RETRIEVAL, ids=req.ids(), attributes={
                "retrieval.mode": req.mode, "retrieval.k": req.k,
                "retrieval.max_rounds": req.max_rounds if iterative else None}) as span:
            if iterative:  # phase 10A: the same loop, with a span per round (services/retrieval/rounds.py)
                r = TracedIterativeRetriever(req.max_rounds, self.aliases, ids=req.ids())(self.stack, req.query, req.k)
            else:
                r = retrieve(self.stack, req.query, req.k)
            trace = getattr(r, "trace", None)
            span.set({"retrieval.evidence_count": len(r.evidence), "retrieval.temporal.kind": r.resolution.intent.kind,
                      "retrieval.rounds": trace.iterations if trace else None,
                      "retrieval.stop_reason": trace.stop_reason if trace else None})
            log.info("retrieve job=%s session=%s mode=%s evidence=%d%s", req.job_id, req.session_id, req.mode,
                     len(r.evidence), f" rounds={trace.iterations}" if trace else "")
            return RetrievalResponse.of(req, r)

    def health(self) -> dict:
        return {"status": "ok", "service": "retrieval", "chunks": len(self.stack.chunks),
                "reranker": self.stack.reranker is not None}
