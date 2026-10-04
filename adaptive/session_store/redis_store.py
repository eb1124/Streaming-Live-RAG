"""Redis implementation of SessionStore (AdaptiveRAG phase 5). The only module that imports the Redis client
(optional dependency: pip install -e ".[sessions]").

  store = RedisSessionStore.from_env()            # REDIS_URL, e.g. redis://localhost:6379/0
  store = RedisSessionStore(url)                  # or an explicit URL

One Redis string per session: key "adaptiverag:session:<session_id>", value encode_session(session) (versioned JSON,
codec.py). No expiry: a session stays until it is deleted. get() decodes on every call, so each job works on a fresh
Session object and put() writes the whole session back after the turn. No locking: phase 4 runs one worker per queue.
`client` may be any object with get/set/delete (the tests pass an in-process stand-in).
"""

from __future__ import annotations

import os

from adaptive.session.state import Session

from .codec import decode_session, encode_session

KEY_PREFIX = "adaptiverag:session:"


class SessionStoreConfigError(RuntimeError):
    pass


class RedisSessionStore:
    def __init__(self, url: str | None = None, client=None, prefix: str = KEY_PREFIX):
        if client is None:
            if not url:
                raise SessionStoreConfigError("RedisSessionStore needs a Redis URL (REDIS_URL) or a client")
            import redis

            client = redis.Redis.from_url(url)
        self.client, self.prefix = client, prefix

    @classmethod
    def from_env(cls, var: str = "REDIS_URL") -> "RedisSessionStore":
        url = os.environ.get(var)
        if not url:
            raise SessionStoreConfigError(f"{var} is not set")
        return cls(url)

    def key(self, session_id: str) -> str:
        return self.prefix + session_id

    def get(self, session_id: str) -> Session | None:
        body = self.client.get(self.key(session_id))
        return None if body is None else decode_session(body)

    def put(self, session_id: str, session: Session) -> None:
        self.client.set(self.key(session_id), encode_session(session))

    def delete(self, session_id: str) -> None:
        self.client.delete(self.key(session_id))
