"""Clause numbers: which enumerators define document structure, and how they nest.

Structural (a clause boundary):
  decimal    5.1  5.1.1  4.4.3.1  11.4.9  5002.1   (a trailing ".0" is dropped: "11.0" -> "11")
  prefixed   PR1  PR1.1                                (McGill)
  integer    5                                          (only in the contexts described in chunker.py)
Local (list items inside a clause, never a boundary): a. b. / i. ii. / (1) (a) / bullets.

Numbers are compared as tuples of components: "5.1.1" -> ("5", "1", "1"), "PR1.1" -> ("PR1", "1").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Sub-components are 1-99 without a leading zero: "2054.517" / "552.021" at a line start are
# statute citations ("Section 2054.517 of the Texas Government Code"), not clause numbers.
DECIMAL = re.compile(r"^\d{1,4}(?:\.(?:[1-9]\d?|0))+$")
PREFIXED = re.compile(r"^[A-Z]{1,3}\d{1,3}(?:\.[1-9]\d?)*$")
INTEGER = re.compile(r"^\d{1,4}$")
# "UT-IRUSP Standard 8: Malware Prevention" implies clauses numbered 8.x (used for diagnostics only).
IMPLIED_NUMBER = re.compile(r"\b(?:Standard|Section|Chapter|Article|Part)\s+(\d{1,3})\b")


@dataclass(frozen=True)
class ClauseNumber:
    parts: tuple[str, ...]
    kind: str  # decimal | prefixed | integer

    @property
    def label(self) -> str:
        return ".".join(self.parts)

    @property
    def root(self) -> str:
        return self.parts[0]

    def is_ancestor_of(self, other: "ClauseNumber") -> bool:
        return len(self.parts) < len(other.parts) and other.parts[: len(self.parts)] == self.parts


def parse_number(number: str | None, marker: str | None) -> ClauseNumber | None:
    """Parse an ingestion block's enumerator. Returns None for local enumerators and bullets."""
    if not number:
        return None
    if marker and marker.startswith("("):
        return None
    n = number.rstrip(".")
    if DECIMAL.match(n):
        parts = tuple(n.split("."))
        while len(parts) > 1 and parts[-1] == "0":
            parts = parts[:-1]
        return ClauseNumber(parts, "decimal" if len(parts) > 1 else "integer")
    if PREFIXED.match(n):
        return ClauseNumber(tuple(n.split(".")), "prefixed")
    if INTEGER.match(n):
        return ClauseNumber((n,), "integer")
    return None


def implied_number(heading_text: str) -> str | None:
    m = IMPLIED_NUMBER.search(heading_text)
    return m.group(1) if m else None
