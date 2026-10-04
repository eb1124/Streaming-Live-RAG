"""Service configuration (phase 7): environment variables only, local-development defaults, no credentials in code.

  API_HOST, API_PORT                  the api (default 127.0.0.1:8000)
  RETRIEVAL_HOST, RETRIEVAL_PORT      the retrieval service (default 127.0.0.1:8001)
  GENERATION_HOST, GENERATION_PORT    the generation service (default 127.0.0.1:8002)
  RETRIEVAL_URL, GENERATION_URL       where the orchestrator finds them (default: from the host/port above)
  RABBITMQ_URL                        the phase 4 queue (api and orchestrator; required when they run apart)
  REDIS_URL                           the phase 5 session store (api and orchestrator; required when they run apart)
  RETRIEVAL_MODE                      single (the frozen retrieval, default) | iterative (the phase 6 loop)
  MAX_ROUNDS                          phase 6 round budget (default 3)
  GENERATION_PROVIDER                 groq (default; GROQ_API_KEY) | offline (stub model: always abstains, no tokens)
  QUERY_TIMEOUT_S                     how long the api waits for a job's final event (default 180)
  SERVICE_TIMEOUT_S                   HTTP timeout of orchestrator -> retrieval / generation calls (default 120)
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    retrieval_host: str = "127.0.0.1"
    retrieval_port: int = 8001
    generation_host: str = "127.0.0.1"
    generation_port: int = 8002
    retrieval_url: str = "http://127.0.0.1:8001"
    generation_url: str = "http://127.0.0.1:8002"
    rabbitmq_url: str | None = None
    redis_url: str | None = None
    retrieval_mode: str = "single"
    max_rounds: int = 3
    generation_provider: str = "groq"
    query_timeout_s: float = 180.0
    service_timeout_s: float = 120.0

    @classmethod
    def from_env(cls) -> "Settings":
        rh, rp = _env("RETRIEVAL_HOST", "127.0.0.1"), int(_env("RETRIEVAL_PORT", "8001"))
        gh, gp = _env("GENERATION_HOST", "127.0.0.1"), int(_env("GENERATION_PORT", "8002"))
        s = cls(api_host=_env("API_HOST", "127.0.0.1"), api_port=int(_env("API_PORT", "8000")),
                retrieval_host=rh, retrieval_port=rp, generation_host=gh, generation_port=gp,
                retrieval_url=_env("RETRIEVAL_URL", f"http://{rh}:{rp}"),
                generation_url=_env("GENERATION_URL", f"http://{gh}:{gp}"),
                rabbitmq_url=_env("RABBITMQ_URL"), redis_url=_env("REDIS_URL"),
                retrieval_mode=_env("RETRIEVAL_MODE", "single"), max_rounds=int(_env("MAX_ROUNDS", "3")),
                generation_provider=_env("GENERATION_PROVIDER", "groq"),
                query_timeout_s=float(_env("QUERY_TIMEOUT_S", "180")),
                service_timeout_s=float(_env("SERVICE_TIMEOUT_S", "120")))
        if s.retrieval_mode not in ("single", "iterative"):
            raise ValueError(f"RETRIEVAL_MODE must be single or iterative, not {s.retrieval_mode!r}")
        if s.generation_provider not in ("groq", "offline"):
            raise ValueError(f"GENERATION_PROVIDER must be groq or offline, not {s.generation_provider!r}")
        return s

    def require(self, *names: str) -> None:
        missing = [n.upper() for n in names if not getattr(self, n)]
        if missing:
            raise SystemExit(f"missing configuration: {', '.join(missing)} (see services/config.py)")
