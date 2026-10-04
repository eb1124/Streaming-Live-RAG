"""Session serialization (AdaptiveRAG phase 5): the phase 3 Session as versioned JSON, and back.

  body = encode_session(session)       # bytes
  session = decode_session(body)       # Session, Turn, Resolution, MultiIntentAnswer, AdaptiveAnswer, Citation ...

Envelope: {"format": "adaptiverag.session", "version": 1, "session": <Session>}. The session is the complete phase 3
state, field for field, as the dataclasses define it (pydantic TypeAdapter over the existing dataclasses: nothing in
Session, Turn or Resolution is redesigned). Decoding rebuilds the same classes, validated against their field types,
so a decoded session resolves follow-ups exactly like the one that was encoded. JSON only: no pickle, no code
executed on load. A body with another format or version is refused (SessionFormatError), never guessed at.
"""

from __future__ import annotations

import json

from pydantic import TypeAdapter, ValidationError

from adaptive.session.state import Session

FORMAT = "adaptiverag.session"
VERSION = 1
_SESSION = TypeAdapter(Session)


class SessionFormatError(ValueError):
    pass


def encode_session(session: Session) -> bytes:
    envelope = {"format": FORMAT, "version": VERSION, "session": _SESSION.dump_python(session, mode="json")}
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode_session(body: bytes | str) -> Session:
    try:
        envelope = json.loads(body)
    except ValueError as e:
        raise SessionFormatError(f"not JSON: {e}") from e
    if not isinstance(envelope, dict) or envelope.get("format") != FORMAT:
        raise SessionFormatError("not an adaptiverag session")
    if envelope.get("version") != VERSION:
        raise SessionFormatError(f"session format version {envelope.get('version')!r}, expected {VERSION}")
    try:
        return _SESSION.validate_python(envelope["session"])
    except (KeyError, ValidationError) as e:
        raise SessionFormatError(f"invalid session: {e}") from e
