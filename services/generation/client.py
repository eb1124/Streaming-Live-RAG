"""Generation service client (phase 7): a drop-in for GroundedAnswerer on the orchestrator side.

  answerer = GenerationClient(httpx.Client(base_url=GENERATION_URL))
  SessionController(catalog, provider, answerer=answerer, ...)        # phase 1's answerer hook

`answerer.answer(question, evidence, question_versions=...)` has GroundedAnswerer.answer's signature and returns the
GroundedAnswer the service produced, with the bound correlation ids on the request. Errors raise ServiceError.
"""

from __future__ import annotations

import httpx

from generation.answer import GroundedAnswer
from generation.context import Evidence

from .. import correlation
from ..contracts import EvidenceItem, GenerationRequest, GenerationResponse
from ..http import post


class GenerationClient:
    def __init__(self, http: httpx.Client):
        self.http = http

    def answer(self, question: str, evidence: list[Evidence],
               question_versions: dict[str, list[str]] | None = None) -> GroundedAnswer:
        req = GenerationRequest(**correlation.current(), question=question,
                                evidence=[EvidenceItem.of(e) for e in evidence], question_versions=question_versions)
        return post(self.http, "/generate", req, GenerationResponse).answer


class NoLocalModel:
    """The orchestrator's provider slot: generation runs in the generation service, so a local model call is a bug."""

    name, model = "generation-service", None

    def complete(self, system, user, schema):
        raise RuntimeError("the orchestrator never calls a model; generation runs in the generation service")
