"""The process's tracer provider (phase 10A).

  configure("adaptiverag-api")    once per process, from the service's __main__; a second call changes nothing
  tracer()                        the tracer every span helper uses

The provider is kept here, not registered as OpenTelemetry's global one: the global can be set only once per
process, which would make tests interfere with one another. Without a configured provider tracer() is the
OpenTelemetry API's own tracer: a no-op, unless something else set a global provider. The SDK is imported only when
an exporter is configured; a missing SDK or exporter package, or an invalid setting, is logged and leaves tracing
off: configure never raises.
"""

from __future__ import annotations

import logging
import sys
import threading

from opentelemetry import trace

from .config import TelemetrySettings

log = logging.getLogger(__name__)
SCOPE = "adaptiverag"

_lock = threading.Lock()
_provider = None  # the SDK TracerProvider; None: tracing is off
_configured = False


def _build(settings: TelemetrySettings):
    if not settings.enabled:
        return None
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor

    if settings.exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        processor = BatchSpanProcessor(OTLPSpanExporter())  # endpoint, headers, timeout: OTEL_EXPORTER_OTLP_*
    else:  # stderr: stdout is the protocol channel of the MCP stdio transport
        processor = SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stderr))
    provider = TracerProvider(resource=Resource.create({"service.name": settings.service_name}))
    provider.add_span_processor(processor)
    return provider


def configure(service: str, settings: TelemetrySettings | None = None) -> bool:
    """Set up tracing for this process; True when spans are recorded. Idempotent: only the first call configures."""
    global _provider, _configured
    with _lock:
        if _configured:
            return _provider is not None
        _configured = True
        try:
            settings = settings or TelemetrySettings.from_env(service)
            _provider = _build(settings)
        except Exception as e:  # tracing must never stop a service from starting
            log.warning("tracing is off: %s: %s", type(e).__name__, e)
            _provider = None
        else:
            log.info("tracing %s", f"on: service {settings.service_name}, exporter {settings.exporter}"
                     if _provider is not None else "off (OTEL_TRACES_EXPORTER=none)")
        return _provider is not None


def active() -> bool:
    return _provider is not None


def tracer():
    provider = _provider
    return trace.get_tracer(SCOPE) if provider is None else provider.get_tracer(SCOPE)


def install(provider):
    """Replace the process's provider and return the previous one (tests: services/telemetry/testing.py)."""
    global _provider
    with _lock:
        previous, _provider = _provider, provider
        return previous


def shutdown() -> None:
    """Flush and drop the provider; configure() can then run again."""
    global _provider, _configured
    with _lock:
        provider, _provider, _configured = _provider, None, False
    if provider is not None:
        try:
            provider.shutdown()
        except Exception as e:
            log.warning("tracing shutdown failed: %s: %s", type(e).__name__, e)
