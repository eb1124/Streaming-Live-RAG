"""python -m services.orchestrator    the orchestrator: consumes phase 4 jobs from RabbitMQ (RABBITMQ_URL), keeps sessions
in Redis (REDIS_URL), and calls the retrieval (RETRIEVAL_URL) and generation (GENERATION_URL) services.
RETRIEVAL_MODE=iterative runs the phase 6 loop in the retrieval service (MAX_ROUNDS). Runs until interrupted.
"""

import logging


def main() -> int:
    import httpx

    from adaptive.jobs.rabbitmq import RabbitMQBroker
    from adaptive.session_store.redis_store import RedisSessionStore

    from .. import telemetry
    from ..config import Settings
    from ..generation.client import GenerationClient
    from ..retrieval.client import RetrievalClient
    from .worker import OrchestratorWorker, build_controller, load_catalog

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    telemetry.configure(telemetry.ORCHESTRATOR)
    s = Settings.from_env()
    s.require("rabbitmq_url", "redis_url")
    retrieval = RetrievalClient(httpx.Client(base_url=s.retrieval_url, timeout=s.service_timeout_s), s.retrieval_mode,
                                s.max_rounds)
    generation = GenerationClient(httpx.Client(base_url=s.generation_url, timeout=s.service_timeout_s))
    broker = RabbitMQBroker(s.rabbitmq_url)
    worker = OrchestratorWorker(build_controller(load_catalog(), retrieval, generation), broker,
                                sessions=RedisSessionStore(s.redis_url))
    logging.getLogger(__name__).info("orchestrator consuming %s; retrieval %s (%s), generation %s, sessions in Redis",
                                     worker.jobs_queue, s.retrieval_url, s.retrieval_mode, s.generation_url)
    try:
        worker.run()
    except KeyboardInterrupt:
        pass
    finally:
        broker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
