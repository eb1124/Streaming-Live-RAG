"""API HTTP interface (phase 7). Routes validate and delegate; no retrieval, answering or session logic lives here.

  GET  /health   {"status": "ok", "service": "api"}
  POST /query    QueryRequest {"session_id", "question", "request_id"?} -> QueryResponse
                 200 completed (answered or abstained) | 422 invalid request | 502 the job failed | 504 no result in time
  GET  /sessions/{session_id}     (phase 10B) the stored session, read only:
                 {"schema_version", "session_id", "turns": [Turn.to_dict()]}; 404 when the store has no such session.
                 Each turn is what the orchestrator stored: the question, its resolution, and the phase 2 answer with
                 its decomposition, per-intent records, claims, citations and phase 1 answers.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Path, Response

from .. import telemetry
from ..contracts import SCHEMA_VERSION, QueryRequest, QueryResponse
from ..orchestrator.client import OrchestratorClient, QueryTimeout

SESSION_ID = Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")  # QueryRequest.session_id's rule


def create_app(orchestrator: OrchestratorClient) -> FastAPI:
    app = FastAPI(title="AdaptiveRAG API", version="1")
    app.add_middleware(telemetry.TracingMiddleware, service=telemetry.API)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "api"}

    @app.post("/query", response_model=QueryResponse)
    def query(req: QueryRequest, response: Response) -> QueryResponse:
        try:
            out = orchestrator.query(req)
        except QueryTimeout as e:
            raise HTTPException(status_code=504, detail=str(e)) from e
        if out.status == "failed":
            response.status_code = 502
        return out

    @app.get("/sessions/{session_id}")
    def session(session_id: str = SESSION_ID) -> dict:
        telemetry.annotate(ids={"session_id": session_id})
        stored = orchestrator.session(session_id)
        if stored is None:
            raise HTTPException(status_code=404, detail=f"no stored session {session_id!r}")
        return {"schema_version": SCHEMA_VERSION, "session_id": session_id, **stored}

    return app
