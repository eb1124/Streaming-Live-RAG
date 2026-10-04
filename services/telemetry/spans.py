"""Span helpers (phase 10A): start a span, attach attributes, record a failure. Nothing here raises.

  with span("generation.execute", GENERATION, ids=req.ids(), attributes={"generation.evidence_count": n}) as sp:
      ...                                 # an exception is recorded on the span and re-raised unchanged
      sp.set({"generation.status": a.status})
  sp = start("retrieval.round", RETRIEVAL); ...; sp.end()      # not made current: for spans without children
  annotate(ids={...})                     # attributes on the span that is current (e.g. the HTTP request's)

Attributes. Only the names in ATTRIBUTES are ever set, with scalar values (text cut at MAX_TEXT); anything else is
dropped. No name stands for a payload: queries, questions, answers, prompts, chunk and document text, headers,
SQL and connection strings have no attribute. Add a name here before using it.

Exceptions. A failure is an "exception" event with the exception's type, the attribute error.type and the status
ERROR. The message and the traceback are not recorded: messages can quote payloads (a validation error echoes its
input, a ServiceError the downstream response). They stay in the logs, as before.

Correlation. Every span gets the ids bound in services.correlation, or the `ids` given (a request's own):
adaptiverag.request_id / job_id / session_id, and correlation_id = the job id when the work belongs to a job (the
id every downstream call of the job carries), otherwise the request id.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from .. import correlation
from . import provider

log = logging.getLogger(__name__)
MAX_TEXT = 256
_KINDS = {"internal": SpanKind.INTERNAL, "server": SpanKind.SERVER}

ATTRIBUTES = frozenset({
    # every span
    "correlation_id", "adaptiverag.request_id", "adaptiverag.job_id", "adaptiverag.session_id",
    "adaptiverag.service", "adaptiverag.operation", "error.type",
    # HTTP server spans
    "http.request.method", "http.route", "http.response.status_code",
    # orchestrator.execute
    "job.status", "session.turn_index", "session.continued", "orchestrator.resolution",
    "orchestrator.answer_status", "orchestrator.abstention_reason", "orchestrator.citation_count",
    # orchestrator.execute: the turn's decision and the lineage of its answer (read from the stored turn)
    "orchestrator.decision", "orchestrator.retrieval_required", "orchestrator.strategy", "orchestrator.claim_count",
    "session.anchor_turn_index", "orchestrator.answer_version",
    # retrieval.execute
    "retrieval.mode", "retrieval.k", "retrieval.max_rounds", "retrieval.evidence_count", "retrieval.rounds",
    "retrieval.stop_reason", "retrieval.temporal.kind",
    # retrieval.round
    "retrieval.round.number", "retrieval.round.strategy", "retrieval.round.retrieved_count",
    "retrieval.round.new_count", "retrieval.round.excluded_count", "retrieval.round.retained_count",
    "retrieval.round.context_count", "retrieval.round.promoted_count", "retrieval.round.decision",
    "retrieval.coverage.decision", "retrieval.coverage.achieved",
    # generation.execute
    "generation.provider", "generation.model", "generation.evidence_count", "generation.status",
    "generation.abstention_reason", "generation.citation_count", "generation.claim_count",
    "generation.verification", "generation.verification.problem_count", "generation.llm_calls",
    "generation.tokens.prompt", "generation.tokens.completion", "generation.tokens.total",
    # mcp.tool.execute
    "mcp.tool.name", "mcp.error.code",
    # db.query
    "db.system.name", "db.operation.name", "db.query.name", "db.namespace", "db.response.returned_rows",
    "db.query.truncated",
})


def _failed(what: str) -> None:
    log.debug("tracing failed (%s); the application is not affected", what, exc_info=True)


def safe_attributes(attributes: dict | None) -> dict:
    out = {}
    for key, value in (attributes or {}).items():
        if key not in ATTRIBUTES or value is None:
            continue
        if isinstance(value, str):
            out[key] = value[:MAX_TEXT]
        elif isinstance(value, (bool, int, float)):
            out[key] = value
    return out


def correlation_attributes(ids: dict | None = None) -> dict:
    merged = {**correlation.current(), **{k: v for k, v in (ids or {}).items() if v is not None}}
    return {"correlation_id": merged.get("job_id") or merged.get("request_id"),
            **{f"adaptiverag.{k}": merged.get(k) for k in correlation.FIELDS}}


class Span:
    """What span() and start() return. Every method is safe with tracing off and never raises."""

    def __init__(self, span=None):
        self._span, self._token = span, None

    def set(self, attributes: dict) -> None:
        if self._span is None:
            return
        try:
            for key, value in safe_attributes(attributes).items():
                self._span.set_attribute(key, value)
        except Exception:
            _failed("set")

    def correlate(self, ids: dict) -> None:
        if self._span is not None:
            try:
                self.set(correlation_attributes(ids))
            except Exception:
                _failed("correlate")

    def rename(self, name: str) -> None:
        if self._span is None:
            return
        try:
            self._span.update_name(name)
            self.set({"adaptiverag.operation": name})
        except Exception:
            _failed("rename")

    def error(self, kind: str) -> None:
        """Status ERROR with a short, payload-free kind (an exception type, a tool error code, an HTTP status)."""
        if self._span is None:
            return
        try:
            kind = str(kind)[:MAX_TEXT]
            self._span.set_attribute("error.type", kind)
            self._span.set_status(Status(StatusCode.ERROR, kind))
        except Exception:
            _failed("error")

    def fail(self, exc: BaseException) -> None:
        """Record an exception: its type only (see the module docstring)."""
        if self._span is None:
            return
        try:
            kind = type(exc)
            self._span.add_event("exception", {"exception.type": f"{kind.__module__}.{kind.__qualname__}"})
        except Exception:
            _failed("fail")
        self.error(type(exc).__name__)

    def end(self) -> None:
        if self._span is None:
            return
        try:
            if self._token is not None:
                otel_context.detach(self._token)
                self._token = None
            self._span.end()
        except Exception:
            _failed("end")

    def _attach(self) -> None:
        if self._span is None:
            return
        try:
            self._token = otel_context.attach(trace.set_span_in_context(self._span))
        except Exception:
            _failed("attach")

    def _context(self):
        """The span's own context, or None when it records nothing. A no-op span under a caller's `traceparent`
        carries the caller's ids, not ids of its own: it must not be reported as a span of this service."""
        if self._span is None or not self._span.is_recording():
            return None
        return self._span.get_span_context()

    @property
    def trace_id(self) -> str | None:
        """32 hex digits, None without a recording span. For logs; no application logic depends on it."""
        try:
            c = self._context()
            return f"{c.trace_id:032x}" if c is not None and c.is_valid else None
        except Exception:
            return None

    @property
    def span_id(self) -> str | None:
        try:
            c = self._context()
            return f"{c.span_id:016x}" if c is not None and c.is_valid else None
        except Exception:
            return None


def start(name: str, service: str, attributes: dict | None = None, ids: dict | None = None,
          kind: str = "internal", parent=None) -> Span:
    """A started span, a child of the current one (or of `parent`, an extracted context). The caller ends it."""
    try:
        attrs = safe_attributes({**correlation_attributes(ids), "adaptiverag.service": service,
                                 "adaptiverag.operation": name, **(attributes or {})})
        return Span(provider.tracer().start_span(name, context=parent, kind=_KINDS[kind], attributes=attrs))
    except Exception:
        _failed("start")
        return Span()


@contextmanager
def span(name: str, service: str, attributes: dict | None = None, ids: dict | None = None,
         kind: str = "internal", parent=None):
    """A span that is current inside the block. An exception is recorded on it and re-raised unchanged."""
    s = start(name, service, attributes, ids, kind, parent)
    s._attach()
    try:
        yield s
    except Exception as e:
        s.fail(e)
        raise
    finally:
        s.end()


def annotate(attributes: dict | None = None, ids: dict | None = None) -> None:
    """Attributes (and correlation ids) on the current span; nothing when there is none."""
    try:
        current = Span(trace.get_current_span())
        if ids is not None:
            current.correlate(ids)
        current.set(attributes or {})
    except Exception:
        _failed("annotate")
