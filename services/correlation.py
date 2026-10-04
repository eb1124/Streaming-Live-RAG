"""Correlation identifiers across service calls (phase 7).

The orchestrator worker binds the job's ids (job_id, session_id) while it handles the job; the retrieval and generation
clients read them and put them into every request, and the services echo them in their responses. A ContextVar, so
the binding is per thread / task and never leaks from one job into the next.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

_IDS: ContextVar[dict] = ContextVar("adaptiverag_correlation", default={})
FIELDS = ("request_id", "job_id", "session_id")


def current() -> dict:
    """The bound ids: {"request_id", "job_id", "session_id"}, each None when not bound."""
    ids = _IDS.get()
    return {k: ids.get(k) for k in FIELDS}


@contextmanager
def bind(**ids):
    unknown = set(ids) - set(FIELDS)
    if unknown:
        raise ValueError(f"unknown correlation ids {sorted(unknown)}")
    token = _IDS.set({**_IDS.get(), **{k: v for k, v in ids.items() if v is not None}})
    try:
        yield
    finally:
        _IDS.reset(token)
