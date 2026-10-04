"""Retrieval service HTTP interface (phase 7). Routes only delegate to RetrievalService.

  GET  /health     {"status": "ok", "service": "retrieval", "chunks": n, "reranker": bool}
  POST /retrieve   RetrievalRequest -> RetrievalResponse   (422 on an invalid request)
"""

from __future__ import annotations

from fastapi import FastAPI

from .. import telemetry
from ..contracts import RetrievalRequest, RetrievalResponse
from .service import RetrievalService


def create_app(service: RetrievalService) -> FastAPI:
    app = FastAPI(title="AdaptiveRAG retrieval", version="1")
    app.add_middleware(telemetry.TracingMiddleware, service=telemetry.RETRIEVAL)

    @app.get("/health")
    def health() -> dict:
        return service.health()

    @app.post("/retrieve", response_model=RetrievalResponse)
    def retrieve(req: RetrievalRequest) -> RetrievalResponse:
        return service.handle(req)

    return app
