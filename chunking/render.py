"""Turning blocks into chunk text: tables, sentence boundaries, line continuations."""

from __future__ import annotations

import re

from ingestion.models import Block

# ---------------------------------------------------------------- tables

LOW_TABLE_EMPTY_FRACTION = 0.25
KEY_MAX_WORDS = 5


def assess_table(rows: list[list[str | None]]) -> tuple[str, list[str]]:
    """'medium' = reconstructed by ingestion's table detector and plausible; 'low' = the cell
    relationships are doubtful. Ingestion never marks a detected table 'high'."""
    notes = []
    cells = [c for row in rows for c in row]
    if len(rows) < 2:
        notes.append("fewer than 2 rows")
    empty = sum(1 for c in cells if not (c or "").strip())
    if cells and empty / len(cells) >= LOW_TABLE_EMPTY_FRACTION:
        notes.append(f"{empty}/{len(cells)} cells empty (split or merged cells)")
    first = (rows[0][0] or "").strip() if rows and rows[0] else ""
    if first[:1].islower():
        notes.append("first cell starts mid-sentence (running text detected as a table)")
    return ("low" if notes else "medium"), notes


def _cell(value: str | None) -> str:
    return " ".join((value or "").split()).replace("|", "/")


def is_key_value(rows: list[list[str | None]]) -> bool:
    """Two columns whose first column is a list of short labels ('Effective Date | July 1, 2026')."""
    return bool(rows) and all(
        len(r) == 2
        and r[0]
        and len(r[0].split()) <= KEY_MAX_WORDS
        and not re.match(r"^[\d$]", r[0].strip())
        for r in rows
    )


def render_table(rows: list[list[str | None]], confidence: str, original: str) -> str:
    """Row/column relationships stay explicit: key-value tables as 'Label: value' lines, others as
    a pipe table whose first row is the header. Low-confidence tables keep ingestion's original
    representation, so no relationship between cells is implied that the source does not support."""
    if confidence == "low":
        return original
    if is_key_value(rows):
        return "\n".join(f"{_cell(k)}: {_cell(v)}" for k, v in rows)
    header, *body = rows
    lines = ["| " + " | ".join(_cell(c) for c in header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in body]
    return "\n".join(lines)


# ---------------------------------------------------------------- sentences

ABBREVIATIONS = {
    "e.g", "i.e", "etc", "vs", "no", "nos", "st", "dr", "mr", "ms", "mrs", "inc", "ltd", "co", "u.s",
    "approx", "dept", "sec", "art", "fig", "cf", "al", "jr", "sr",
}
SENTENCE_END = re.compile(r"[.!?][\"”’)]*\s+(?=[\"“‘(\[]?[A-Z0-9•])")
CLAUSE_END = re.compile(r"[;:]\s+")


def _is_abbreviation(text: str, end: int) -> bool:
    word = re.search(r"([\w.]+)$", text[:end])
    if not word:
        return False
    w = word.group(1).lower().rstrip(".")
    return len(w) == 1 or w in ABBREVIATIONS  # a single letter is an initial ("U. T. Austin")


def boundary_spans(text: str, pattern: re.Pattern = SENTENCE_END) -> list[tuple[int, int]]:
    """Split `text` into (start, end) spans at sentence (or clause) boundaries."""
    spans, start = [], 0
    for m in pattern.finditer(text):
        if pattern is SENTENCE_END and _is_abbreviation(text, m.start()):
            continue
        end = m.start() + len(m.group(0).rstrip())
        spans.append((start, end))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return [(s, e) for s, e in spans if text[s:e].strip()]


# ---------------------------------------------------------------- joining blocks


def continues(prev: Block, cur: Block) -> bool:
    """`cur` continues `prev` mid-sentence (a paragraph broken by a page or column boundary)."""
    return (
        prev.kind not in ("heading", "table")
        and cur.kind not in ("heading", "table")
        and not cur.marker
        and cur.text[:1].islower()
    )
