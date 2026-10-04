"""Temporal resolution of a ranked candidate list (post-retrieval; the corpus is never changed).

  resolution = TemporalResolver.from_chunks(chunks).resolve(query, ranked_chunk_ids)

Only chunks belonging to a multi-version series are affected. For those, candidates from versions other
than the selected one(s) are removed; everything else keeps its relative order (stable filter), so
lower-ranked chunks of the selected version move up. Neutral queries return the input unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .intent import TemporalIntent, parse_query
from .versions import VersionRegistry


@dataclass
class Resolution:
    intent: TemporalIntent
    selected: dict[str, list[str]]  # series_id -> allowed doc_ids (only for series that are filtered)
    kept: list[str]
    dropped: list[str]
    flags: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.dropped)


class TemporalResolver:
    def __init__(self, registry: VersionRegistry, doc_of: dict[str, str]):
        self.registry, self.doc_of = registry, doc_of

    @classmethod
    def from_chunks(cls, chunks) -> "TemporalResolver":
        return cls(VersionRegistry.from_chunks(chunks), {c.chunk_id: c.doc_id for c in chunks})

    def select(self, intent: TemporalIntent) -> tuple[dict[str, set[str]], list[str]]:
        allowed: dict[str, set[str]] = {}
        flags: list[str] = []
        for series in self.registry.by_series:
            if intent.kind in ("point_in_time", "compare"):
                docs = {v.doc_id for d in intent.dates for v in self.registry.for_date(series, d)}
                if not docs:
                    flags.append(f"requested_version_unavailable:{series}")
                allowed[series] = docs
            elif intent.kind == "current":
                current = self.registry.current(series)
                if len(current) == 1:
                    allowed[series] = {current[0].doc_id}
                else:
                    flags.append(f"current_version_ambiguous:{series}")
        return allowed, flags

    def resolve(self, query: str, ranked: list[str]) -> Resolution:
        intent = parse_query(query)
        allowed, flags = self.select(intent)
        kept, dropped = [], []
        for cid in ranked:
            series = self.registry.series_of.get(self.doc_of.get(cid, ""))
            if series in allowed and self.doc_of[cid] not in allowed[series]:
                dropped.append(cid)
            else:
                kept.append(cid)
        return Resolution(intent, {s: sorted(d) for s, d in allowed.items()}, kept, dropped, flags)
