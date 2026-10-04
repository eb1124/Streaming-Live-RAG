"""All four services in one process (phase 7): the same components as the separate deployment, wired with FastAPI's
TestClient instead of sockets and the in-memory broker instead of RabbitMQ. For the offline evaluation and for
development without infrastructure; the HTTP contracts, the orchestrator and the phase 4 job lifecycle are the ones
the separate processes use.

  local = InProcess(stack, provider, mode="iterative", sessions=None)
  local.api.post("/query", json={"session_id": "s1", "question": "..."})
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from adaptive.jobs.broker import InMemoryBroker
from adaptive.session_store.store import InMemorySessionStore, SessionStore
from generation.pipeline import RetrievalStack

from .api.app import create_app as api_app
from .generation.app import create_app as generation_app
from .generation.client import GenerationClient
from .generation.service import GenerationService
from .orchestrator.client import OrchestratorClient
from .orchestrator.worker import OrchestratorWorker, build_controller, catalog_stack
from .retrieval.app import create_app as retrieval_app
from .retrieval.client import RetrievalClient
from .retrieval.service import RetrievalService


class InProcess:
    def __init__(self, stack: RetrievalStack, provider, mode: str = "single", max_rounds: int = 3,
                 sessions: SessionStore | None = None, aliases: dict[str, str] | None = None, timeout_s: float = 60):
        self.retrieval = RetrievalService(stack, aliases)
        self.generation = GenerationService(provider)
        self.broker = InMemoryBroker()
        self.sessions = InMemorySessionStore() if sessions is None else sessions
        controller = build_controller(catalog_stack(stack.chunks),
                                      RetrievalClient(TestClient(retrieval_app(self.retrieval)), mode, max_rounds),
                                      GenerationClient(TestClient(generation_app(self.generation))), aliases)
        self.worker = OrchestratorWorker(controller, self.broker, sessions=self.sessions)
        self.orchestrator = OrchestratorClient(lambda: self.broker, self.sessions, timeout_s, pump=self.worker.run)
        self.api = TestClient(api_app(self.orchestrator))
