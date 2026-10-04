"""Local dev stand-in for `python -m services.api` + `python -m services.orchestrator` combined.

Why this exists: the documented deployment (docs/services.md) runs the api and orchestrator as separate
processes connected by RabbitMQ (job queue) and Redis (session store). For local development without that
infrastructure, this script wires the same real api app and real orchestrator worker together in one process,
using the in-memory broker and in-memory session store (adaptive/jobs/broker.py, adaptive/session_store/store.py)
instead -- the same substitution services.inprocess.InProcess makes for tests, but served over real HTTP via
uvicorn instead of TestClient, so the frontend dev server's proxy can reach it like any other service.

Nothing about retrieval or generation is stubbed: this process calls the real retrieval and generation services
over HTTP, exactly as the orchestrator normally does. Run those separately first:

  python -m services.retrieval
  python -m services.generation        (or GENERATION_PROVIDER=offline for no live LLM calls)

then:

  python scripts/run_local_api.py      # api on API_HOST:API_PORT, default 127.0.0.1:8000
"""

import logging

import httpx
import uvicorn

from adaptive.jobs.broker import InMemoryBroker
from adaptive.session_store.store import InMemorySessionStore

from services import telemetry
from services.api.app import create_app
from services.config import Settings
from services.generation.client import GenerationClient
from services.orchestrator.client import OrchestratorClient
from services.orchestrator.worker import OrchestratorWorker, build_controller, load_catalog
from services.retrieval.client import RetrievalClient


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    telemetry.configure(telemetry.API)
    s = Settings.from_env()

    retrieval = RetrievalClient(httpx.Client(base_url=s.retrieval_url, timeout=s.service_timeout_s),
                                s.retrieval_mode, s.max_rounds)
    generation = GenerationClient(httpx.Client(base_url=s.generation_url, timeout=s.service_timeout_s))
    controller = build_controller(load_catalog(), retrieval, generation)

    broker = InMemoryBroker()
    sessions = InMemorySessionStore()
    worker = OrchestratorWorker(controller, broker, sessions=sessions)
    client = OrchestratorClient(lambda: broker, sessions, s.query_timeout_s, pump=worker.run)

    logging.getLogger(__name__).info(
        "local api+orchestrator (in-memory broker/sessions); retrieval %s (%s), generation %s",
        s.retrieval_url, s.retrieval_mode, s.generation_url)
    uvicorn.run(create_app(client), host=s.api_host, port=s.api_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
