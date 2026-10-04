"""AdaptiveRAG phase 10A: OpenTelemetry tracing around the services. Observation only: no span changes what a
service returns, and a tracing failure never reaches the application. See docs/telemetry.md.

  telemetry.configure(telemetry.API)                         once per process (services/*/__main__.py); idempotent
  with telemetry.span("retrieval.execute", telemetry.RETRIEVAL, ids=req.ids(), attributes={...}) as sp:
      ...
      sp.set({"retrieval.evidence_count": n})

  config.py     OTEL_* environment variables
  provider.py   the process's tracer provider: configure, tracer, shutdown
  spans.py      span helpers, the attribute allow-list, correlation ids on spans
  asgi.py       HTTP server spans (TracingMiddleware) and the trace headers of service-to-service calls
  testing.py    an in-memory exporter for tests

Correlation ids (services/correlation.py) stay the application's correlation mechanism; spans carry them as
attributes. RetrievalTrace stays the execution state of the phase 6 loop; a round span carries counts from it.
"""

from .asgi import TracingMiddleware, trace_headers
from .provider import active, configure, shutdown, tracer
from .spans import ATTRIBUTES, Span, annotate, span, start

API = "adaptiverag-api"
ORCHESTRATOR = "adaptiverag-orchestrator"
RETRIEVAL = "adaptiverag-retrieval"
GENERATION = "adaptiverag-generation"
MCP = "adaptiverag-mcp"
PERSISTENCE = "adaptiverag-persistence"  # a component inside a process (the metadata repository), not a process

__all__ = ["API", "ATTRIBUTES", "GENERATION", "MCP", "ORCHESTRATOR", "PERSISTENCE", "RETRIEVAL", "Span", "TracingMiddleware",
           "active", "annotate", "configure", "shutdown", "span", "start", "trace_headers", "tracer"]
