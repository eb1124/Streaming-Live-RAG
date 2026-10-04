"""Document versions from chunk metadata only: series_id, effective_date, superseded_date, is_current.

A *series* is every document sharing a series_id; each document is one version. Only series with two or
more versions are version-resolved. Nothing is inferred from filenames or document titles.

Selection rules (see docs/temporal.md):
  date (full)   the version in force on that day: effective_date <= d and (no superseded_date or d < superseded_date)
  date (month)  the version whose effective_date falls in that month (it names the version); otherwise the
                version in force on the 1st of the month
  current       the version with is_current == true (exactly one required)
If no version matches, the series is selected as empty (flag "requested_version_unavailable") so that no
version of it is presented as the requested one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .intent import DateRef


@dataclass(frozen=True)
class Version:
    doc_id: str
    series_id: str
    effective: date | None
    superseded: date | None
    is_current: bool | None


def _iso(value: str | None) -> date | None:
    """Only full ISO dates count; month-only or missing values give no usable boundary."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


class VersionRegistry:
    def __init__(self, versions: list[Version]):
        self.by_series: dict[str, list[Version]] = {}
        for v in sorted(versions, key=lambda v: (v.series_id, v.effective or date.min, v.doc_id)):
            self.by_series.setdefault(v.series_id, []).append(v)
        self.by_series = {s: vs for s, vs in self.by_series.items() if len(vs) >= 2}
        self.series_of = {v.doc_id: s for s, vs in self.by_series.items() for v in vs}

    @classmethod
    def from_chunks(cls, chunks) -> "VersionRegistry":
        docs = {}
        for c in chunks:
            if c.series_id and c.doc_id not in docs:
                docs[c.doc_id] = Version(c.doc_id, c.series_id, _iso(c.effective_date), _iso(c.superseded_date), c.is_current)
        return cls(list(docs.values()))

    def in_force(self, series: str, d: date) -> list[Version]:
        return [v for v in self.by_series[series]
                if v.effective and v.effective <= d and (v.superseded is None or d < v.superseded)]

    def for_date(self, series: str, ref: DateRef) -> list[Version]:
        if ref.day is None:
            named = [v for v in self.by_series[series]
                     if v.effective and (v.effective.year, v.effective.month) == (ref.year, ref.month)]
            if named:
                return named
        return self.in_force(series, ref.first_day())

    def current(self, series: str) -> list[Version]:
        return [v for v in self.by_series[series] if v.is_current is True]
