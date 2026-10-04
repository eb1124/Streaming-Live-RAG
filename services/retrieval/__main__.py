"""python -m services.retrieval      the retrieval service on RETRIEVAL_HOST:RETRIEVAL_PORT (default 127.0.0.1:8001)

Loads the frozen retrieval stack once (arctic-m dense index, BM25, cross-encoder, temporal resolver), then serves
POST /retrieve and GET /health. Needs the "services" and "retrieval" extras.
"""

import logging
import os


def main() -> int:
    import uvicorn

    from generation.pipeline import load_stack

    from .. import telemetry
    from ..config import Settings
    from .app import create_app
    from .service import RetrievalService

    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    telemetry.configure(telemetry.RETRIEVAL)
    s = Settings.from_env()
    app = create_app(RetrievalService(load_stack(rerank=True)))
    uvicorn.run(app, host=s.retrieval_host, port=s.retrieval_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
