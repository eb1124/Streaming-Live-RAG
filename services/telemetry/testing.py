"""Tracing in tests (phase 10A): an in-memory exporter, no collector and no network. Needs the "telemetry" extra.

  with capture() as spans:
      ...run the code...
      names = [s.name for s in spans.get_finished_spans()]

A fresh SDK provider replaces the process's provider (services/telemetry/provider.py) for the block and the
previous one is restored after it, so tests do not see one another's spans and nothing global is left behind.
"""

from __future__ import annotations

from contextlib import contextmanager

from . import provider as _provider


@contextmanager
def capture(service: str = "adaptiverag-test"):
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    sdk = TracerProvider(resource=Resource.create({"service.name": service}), shutdown_on_exit=False)
    sdk.add_span_processor(SimpleSpanProcessor(exporter))
    previous = _provider.install(sdk)
    try:
        yield exporter
    finally:
        _provider.install(previous)
