"""Generation service logic (phase 7): GenerationRequest -> GenerationResponse.

The frozen generation.answer.GroundedAnswerer, unchanged, on the evidence in the request: the relevance gate, context
assembly, the prompt, the model call and the verifier all run here, so the grounding contract (answer only from the
supplied evidence, abstain otherwise, cite what was verified) is enforced where the model is called. The service
never retrieves. It is the only process that holds the model provider (and its credentials).
"""

from __future__ import annotations

import logging

from generation.answer import GroundedAnswerer
from generation.providers import LLMProvider

from .. import telemetry
from ..contracts import GenerationRequest, GenerationResponse

log = logging.getLogger(__name__)


class _Metered:
    """The provider, with the calls of one request counted and their token usage summed (for the request's span).
    Nothing about a call changes: same arguments, same response, same exceptions."""

    def __init__(self, provider: LLMProvider):
        self._provider = provider
        self.name, self.model = getattr(provider, "name", None), getattr(provider, "model", None)
        self.calls, self.usage = 0, {}

    def complete(self, system: str, user: str, schema: dict):
        self.calls += 1
        response = self._provider.complete(system, user, schema)
        for key, value in (getattr(response, "usage", None) or {}).items():
            if isinstance(value, int) and not isinstance(value, bool):
                self.usage[key] = self.usage.get(key, 0) + value
        return response


def verification_outcome(answer) -> str:
    """What the verifier decided, from the answer: "accepted" (its output passed: an answer, or a well-formed
    insufficient-evidence result), "rejected" (ungrounded_output), "not_run" (no model output to verify: the
    relevance gate, no evidence, or a provider failure)."""
    if answer.status == "answered" or answer.abstention_reason == "model_insufficient_evidence":
        return "accepted"
    return "rejected" if answer.abstention_reason == "ungrounded_output" else "not_run"


class GenerationService:
    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self.answerer = GroundedAnswerer(provider)

    def handle(self, req: GenerationRequest) -> GenerationResponse:
        telemetry.annotate(ids=req.ids())  # the enclosing span: the HTTP request's
        with telemetry.span("generation.execute", telemetry.GENERATION, ids=req.ids(), attributes={
                "generation.provider": getattr(self.provider, "name", None),
                "generation.model": getattr(self.provider, "model", None),
                "generation.evidence_count": len(req.evidence)}) as span:
            meter = _Metered(self.provider)  # phase 10: the model calls and tokens of this request, for its span
            a = GroundedAnswerer(meter).answer(req.question, [e.evidence() for e in req.evidence],
                                               question_versions=req.question_versions)
            span.set({"generation.status": a.status, "generation.abstention_reason": a.abstention_reason,
                      "generation.citation_count": len(a.citations), "generation.claim_count": len(a.claims),
                      "generation.verification": verification_outcome(a),
                      "generation.verification.problem_count": len(a.verification_problems),
                      "generation.llm_calls": meter.calls,
                      "generation.tokens.prompt": meter.usage.get("prompt_tokens"),
                      "generation.tokens.completion": meter.usage.get("completion_tokens"),
                      "generation.tokens.total": meter.usage.get("total_tokens")})
            log.info("generate job=%s session=%s evidence=%d status=%s%s", req.job_id, req.session_id,
                     len(req.evidence), a.status, f" ({a.abstention_reason})" if a.abstention_reason else "")
            return GenerationResponse(**req.ids(), answer=a)

    def health(self) -> dict:
        return {"status": "ok", "service": "generation", "provider": getattr(self.provider, "name", None),
                "model": getattr(self.provider, "model", None)}
