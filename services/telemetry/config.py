"""Tracing configuration (phase 10A): environment variables only, the standard OpenTelemetry names.

  OTEL_TRACES_EXPORTER          none | console | otlp. Default: otlp when OTEL_EXPORTER_OTLP_ENDPOINT or
                                OTEL_EXPORTER_OTLP_TRACES_ENDPOINT is set, otherwise none (no collector is needed)
  OTEL_EXPORTER_OTLP_ENDPOINT   the collector, e.g. http://127.0.0.1:4318 (read by the OTLP exporter itself, with
                                its other OTEL_EXPORTER_OTLP_* variables; OTLP over HTTP/protobuf)
  OTEL_SERVICE_NAME             the resource's service.name (default: the process's own name, adaptiverag-api, ...)
  OTEL_SDK_DISABLED             true: no tracing, whatever the exporter

none: no tracer provider is made and every span is a no-op. console: spans are printed to stderr as they end.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

EXPORTERS = ("none", "console", "otlp")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class TelemetrySettings:
    service_name: str
    exporter: str = "none"
    disabled: bool = False

    def __post_init__(self):
        if self.exporter not in EXPORTERS:
            raise ValueError(f"OTEL_TRACES_EXPORTER must be one of {', '.join(EXPORTERS)}, not {self.exporter!r}")

    @classmethod
    def from_env(cls, service: str) -> "TelemetrySettings":
        endpoint = _env("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or _env("OTEL_EXPORTER_OTLP_ENDPOINT")
        return cls(service_name=_env("OTEL_SERVICE_NAME", service),
                   exporter=_env("OTEL_TRACES_EXPORTER", "otlp" if endpoint else "none").strip().lower(),
                   disabled=_env("OTEL_SDK_DISABLED", "false").strip().lower() == "true")

    @property
    def enabled(self) -> bool:
        return not self.disabled and self.exporter != "none"
