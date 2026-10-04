"""Generation service HTTP interface (phase 7). Routes only delegate to GenerationService.

  GET  /health     {"status": "ok", "service": "generation", "provider": ..., "model": ...}
  POST /generate   GenerationRequest -> GenerationResponse   (422 on an invalid request)
"""

from __future__ import annotations

from fastapi import FastAPI

from .. import telemetry
from ..contracts import GenerationRequest, GenerationResponse
from .service import GenerationService


def create_app(service: GenerationService) -> FastAPI:
    app = FastAPI(title="AdaptiveRAG generation", version="1")
    app.add_middleware(telemetry.TracingMiddleware, service=telemetry.GENERATION)

    @app.get("/health")
    def health() -> dict:
        return service.health()

    @app.post("/generate", response_model=GenerationResponse)
    def generate(req: GenerationRequest) -> GenerationResponse:
        return service.handle(req)

    return app
