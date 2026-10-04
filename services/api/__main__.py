"""python -m services.api    the API on API_HOST:API_PORT (default 127.0.0.1:8000). Submits each query as a phase 4 job
on RabbitMQ (RABBITMQ_URL) and reads the answered turn from the Redis session store (REDIS_URL) the orchestrator
writes. Needs a running orchestrator (python -m services.orchestrator).
"""

import logging


def main() -> int:
    import uvicorn

    from adaptive.jobs.rabbitmq import RabbitMQBroker
    from adaptive.session_store.redis_store import RedisSessionStore

    from .. import telemetry
    from ..config import Settings
    from ..orchestrator.client import OrchestratorClient
    from .app import create_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    telemetry.configure(telemetry.API)
    s = Settings.from_env()
    s.require("rabbitmq_url", "redis_url")
    client = OrchestratorClient(lambda: RabbitMQBroker(s.rabbitmq_url), RedisSessionStore(s.redis_url),
                                s.query_timeout_s)
    uvicorn.run(create_app(client), host=s.api_host, port=s.api_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
