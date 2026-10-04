"""python -m services.generation    the generation service on GENERATION_HOST:GENERATION_PORT (default 127.0.0.1:8002)

GENERATION_PROVIDER=groq (default: GROQ_API_KEY from the environment or the project's .env) or offline (a stub model
that always answers "insufficient_evidence": no network, no tokens; the gate and the verifier still run).
"""

import logging


def provider_from(name: str):
    if name == "offline":
        from adaptive.jobs.__main__ import OfflineProvider

        return OfflineProvider()
    from generation.env import load_env
    from generation.providers import get_provider

    load_env()  # project .env (git-ignored); an exported GROQ_API_KEY takes precedence
    return get_provider(name)


def main() -> int:
    import uvicorn

    from .. import telemetry
    from ..config import Settings
    from .app import create_app
    from .service import GenerationService

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    telemetry.configure(telemetry.GENERATION)
    s = Settings.from_env()
    app = create_app(GenerationService(provider_from(s.generation_provider)))
    uvicorn.run(app, host=s.generation_host, port=s.generation_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
