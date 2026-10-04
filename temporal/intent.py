"""Temporal intent of a query, by deterministic pattern matching (no model, no guessing).

  point_in_time  the query names a date or month ("effective July 1, 2026", "the February 2026
                 procedures", "on March 15, 2026", "2026-07-01", "7/1/2026")
  compare        the query names two or more different dates/months (resolved later; kept if they map
                 to different versions)
  current        no date, but asks for the current rule ("current", "currently", "latest", "now",
                 "in effect today", "up to date")
  neutral        none of the above; a bare year ("2026") is too coarse to identify a version

A named date always takes precedence over "current".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9

_MONTH_NAME = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_TEXT_DATE = re.compile(_MONTH_NAME + r"\.?(?:\s+(\d{1,2})(?:st|nd|rd|th)?)?,?\s+(\d{4})\b", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_CURRENT = re.compile(r"\b(current(?:ly)?|latest|now|today|presently|in effect today|up[- ]to[- ]date)\b", re.IGNORECASE)


@dataclass(frozen=True)
class DateRef:
    """A date named in the query. day is None for month-level references ("July 2026")."""

    year: int
    month: int
    day: int | None
    text: str

    def first_day(self) -> date:
        return date(self.year, self.month, self.day or 1)


@dataclass
class TemporalIntent:
    kind: str  # neutral | point_in_time | compare | current
    dates: list[DateRef] = field(default_factory=list)
    trigger: str = ""  # the matched text, for inspection


def parse_query(query: str) -> TemporalIntent:
    dates: list[DateRef] = []
    for m in _TEXT_DATE.finditer(query):
        name = m.group(1).lower()
        month = MONTHS[name] if name in MONTHS else MONTHS[name[:3]]
        dates.append(DateRef(int(m.group(3)), month, int(m.group(2)) if m.group(2) else None, m.group(0)))
    for m in _ISO_DATE.finditer(query):
        dates.append(DateRef(int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(0)))
    for m in _US_DATE.finditer(query):
        dates.append(DateRef(int(m.group(3)), int(m.group(1)), int(m.group(2)), m.group(0)))
    dates = [d for d in dates if 1 <= d.month <= 12 and (d.day is None or 1 <= d.day <= 31)]
    unique = list(dict.fromkeys(dates))
    if len({(d.year, d.month, d.day) for d in unique}) > 1:
        return TemporalIntent("compare", unique, "; ".join(d.text for d in unique))
    if unique:
        return TemporalIntent("point_in_time", unique[:1], unique[0].text)
    m = _CURRENT.search(query)
    if m:
        return TemporalIntent("current", [], m.group(0))
    return TemporalIntent("neutral")
