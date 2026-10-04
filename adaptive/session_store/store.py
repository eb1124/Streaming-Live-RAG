"""The session-store boundary (AdaptiveRAG phase 5).

  store.get(session_id) -> Session | None      (None: no such session yet)
  store.put(session_id, session)               (replaces the stored session)
  store.delete(session_id)                     (no error if absent)

InMemorySessionStore keeps the Session objects themselves in a dict, in the process: phase 4's behaviour, and the
default. RedisSessionStore (adaptive/session_store/redis_store.py) keeps them encoded (codec.py) in Redis, so they
survive a worker restart; it is the only module that imports the Redis client.
"""

from __future__ import annotations

from typing import Protocol

from adaptive.session.state import Session


class SessionStore(Protocol):
    def get(self, session_id: str) -> Session | None: ...

    def put(self, session_id: str, session: Session) -> None: ...

    def delete(self, session_id: str) -> None: ...


class InMemorySessionStore(dict):
    """session_id -> Session, by reference. A dict, so `store["s1"]`, `len(store)` and iteration work as before."""

    def put(self, session_id: str, session: Session) -> None:
        self[session_id] = session

    def delete(self, session_id: str) -> None:
        self.pop(session_id, None)
