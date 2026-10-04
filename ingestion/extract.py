"""PDF -> pages of visual lines (with fonts, sizes, bboxes) and candidate tables.

Uses PyMuPDF's ``rawdict`` output so we have per-character boxes, which lets us
repair the dropped "tt" ligature found in Word/Calibri exports (the glyph is
extracted as a single "t" that is twice as wide as a normal "t").
"""

from __future__ import annotations

import contextlib
import io
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .models import Line, PdfInfo
from .normalize import BULLET_GLYPHS, normalize_text, repair_calibri

BOLD_NAME_HINTS = ("bold", "semibold", "black", "heavy")
DUPLICATE_TOLERANCE = 5.0  # pt; text-shadow re-renders are spread about +-2pt around the true position
TT_WIDTH_RATIO = 1.6  # a "t" at least this much wider than the median "t" is a lost "tt" ligature


@dataclass
class RawTable:
    bbox: tuple[float, float, float, float]
    rows: list[list[str | None]]  # PyMuPDF cell text (fallback); None = covered by a spanning cell
    cells: list[list[tuple[float, float, float, float] | None]]  # cell boxes, same shape as rows


@dataclass
class ExtractedPage:
    page_number: int
    width: float
    height: float
    rotation: int
    image_count: int
    raw_text: str
    lines: list[Line]
    tables: list[RawTable] = field(default_factory=list)
    repairs: dict[str, int] = field(default_factory=dict)


@dataclass
class _Piece:
    """One PyMuPDF 'line' (a run of spans) before visual-line merging."""

    bbox: list[float]
    raw: str
    repaired: str
    font: str
    size: float
    bold_chars: int
    total_chars: int
    repairs: dict[str, int]


def read_pdf_info(doc: pymupdf.Document) -> PdfInfo:
    meta = doc.metadata or {}

    def clean(value):
        value = (value or "").strip()
        return value or None

    return PdfInfo(
        page_count=doc.page_count,
        producer=clean(meta.get("producer")),
        creator=clean(meta.get("creator")),
        pdf_title=clean(meta.get("title")),
        author=clean(meta.get("author")),
        creation_date=clean(meta.get("creationDate")),
        modification_date=clean(meta.get("modDate")),
        encrypted=bool(doc.is_encrypted),
    )


def _is_bold(span: dict) -> bool:
    return bool(span["flags"] & 16) or any(h in span["font"].lower() for h in BOLD_NAME_HINTS)


def _t_widths(raw: dict) -> dict[tuple[str, float], float]:
    """Median width of the letter 't' per (font, size) on this page."""
    samples: dict[tuple[str, float], list[float]] = {}
    for block in raw["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                key = (span["font"], round(span["size"], 1))
                for ch in span["chars"]:
                    if ch["c"] == "t":
                        samples.setdefault(key, []).append(ch["bbox"][2] - ch["bbox"][0])
    return {k: statistics.median(v) for k, v in samples.items() if len(v) >= 5}


def _is_calibri(font: str, extra_fonts: tuple[str, ...]) -> bool:
    name = font.lower()
    return "calibri" in name or any(f.lower() in name for f in extra_fonts)


def _pieces(raw: dict, calibri_fonts: tuple[str, ...] = ()) -> list[_Piece]:
    t_width = _t_widths(raw)
    pieces = []
    for block in raw["blocks"]:
        for line in block.get("lines", []):
            raw_parts, fixed_parts = [], []
            font_chars: dict[tuple[str, float], int] = {}
            bold = total = 0
            repairs: dict[str, int] = {}
            for span in line["spans"]:
                key = (span["font"], round(span["size"], 1))
                ref = t_width.get(key)
                raw_text = "".join(c["c"] for c in span["chars"])
                fixed = []
                for ch in span["chars"]:
                    c = ch["c"]
                    if c == "t" and ref and (ch["bbox"][2] - ch["bbox"][0]) >= TT_WIDTH_RATIO * ref:
                        c = "tt"
                        repairs["lost_tt_ligature"] = repairs.get("lost_tt_ligature", 0) + 1
                    fixed.append(c)
                fixed_text = "".join(fixed)
                if _is_calibri(span["font"], calibri_fonts):
                    fixed_text, n = repair_calibri(fixed_text)
                    if n:
                        repairs["calibri_ligature"] = repairs.get("calibri_ligature", 0) + n
                raw_parts.append(raw_text)
                fixed_parts.append(fixed_text)
                n_chars = len(raw_text.strip())
                font_chars[key] = font_chars.get(key, 0) + n_chars
                total += n_chars
                if _is_bold(span):
                    bold += n_chars
            raw_joined = "".join(raw_parts)
            if not raw_joined.strip():
                continue
            font, size = max(font_chars, key=font_chars.get) if font_chars else ("", 0.0)
            pieces.append(
                _Piece(list(line["bbox"]), raw_joined, "".join(fixed_parts), font, size, bold, total, repairs)
            )
    return pieces


def _split_duplicates(pieces: list[_Piece]) -> tuple[list[_Piece], list[_Piece]]:
    """Separate exact re-renders of the same text at (almost) the same position."""
    kept: list[_Piece] = []
    dups: list[_Piece] = []
    for p in pieces:
        is_dup = any(
            k.raw == p.raw
            and abs(k.bbox[0] - p.bbox[0]) <= DUPLICATE_TOLERANCE
            and abs(k.bbox[1] - p.bbox[1]) <= DUPLICATE_TOLERANCE
            for k in kept
        )
        (dups if is_dup else kept).append(p)
    return kept, dups


def _v_overlap(a: list[float], b: list[float]) -> float:
    inter = min(a[3], b[3]) - max(a[1], b[1])
    smaller = min(a[3] - a[1], b[3] - b[1]) or 1.0
    return max(0.0, inter) / smaller


def _merge_visual_lines(pieces: list[_Piece]) -> list[_Piece]:
    """Join pieces that sit on the same baseline and touch horizontally (links, bullets, styled runs)."""
    pieces = sorted(pieces, key=lambda p: ((p.bbox[1] + p.bbox[3]) / 2, p.bbox[0]))
    merged: list[_Piece] = []
    for p in pieces:
        target = None
        for m in reversed(merged[-6:]):
            if _v_overlap(m.bbox, p.bbox) < 0.6:
                continue
            gap = p.bbox[0] - m.bbox[2]
            size = max(p.size, m.size, 1.0)
            is_bullet = m.repaired.strip() and all(ch in BULLET_GLYPHS for ch in m.repaired.strip())
            # Whitespace at the boundary signals running text split by styling/links, which
            # tolerates a wider gap. Without it, stay tight so table cells are not glued together.
            spaced = m.raw.endswith(" ") or p.raw.startswith(" ")
            if gap >= -2.0 and (
                gap <= max(1.0 * size, 4.0)
                or (spaced and gap <= 2.5 * size)
                or (is_bullet and gap <= 3 * size)
            ):
                target = m
                break
        if target is None:
            merged.append(_Piece(p.bbox[:], p.raw, p.repaired, p.font, p.size, p.bold_chars, p.total_chars, dict(p.repairs)))
            continue
        gap = p.bbox[0] - target.bbox[2]
        left, right = target.raw.rstrip(), p.raw.lstrip()
        spaced = target.raw.endswith(" ") or p.raw.startswith(" ")
        # A separate fragment starting with a capital after a letter is a new word even when the
        # boxes touch (link text split across re-renders: "Approval" + "Process").
        word_break = bool(left) and bool(right) and left[-1].isalpha() and right[0].isupper()
        sep = " " if not spaced and (gap > 0.2 * p.size or word_break) else ""
        target.raw += sep + p.raw
        target.repaired += sep + p.repaired
        target.bbox = [min(target.bbox[0], p.bbox[0]), min(target.bbox[1], p.bbox[1]),
                       max(target.bbox[2], p.bbox[2]), max(target.bbox[3], p.bbox[3])]
        if p.total_chars > target.total_chars:
            target.font, target.size = p.font, p.size
        target.bold_chars += p.bold_chars
        target.total_chars += p.total_chars
        for k, v in p.repairs.items():
            target.repairs[k] = target.repairs.get(k, 0) + v
    return merged


def _to_line(piece: _Piece, page_number: int, idx: int, removed: str | None = None) -> Line:
    text, norm_repairs = normalize_text(piece.repaired)
    repairs = [f"{k}:{v}" for k, v in sorted(piece.repairs.items())] + norm_repairs
    return Line(
        line_id=f"p{page_number}-l{idx}",
        page=page_number,
        bbox=tuple(round(v, 2) for v in piece.bbox),
        raw_text=piece.raw,
        text=text,
        font=piece.font,
        size=round(piece.size, 2),
        bold_ratio=round(piece.bold_chars / piece.total_chars, 2) if piece.total_chars else 0.0,
        removed=removed,
        repairs=repairs,
    )


def _find_tables(page: pymupdf.Page) -> list[RawTable]:
    tables = []
    # find_tables prints an advisory about an optional package to stdout; keep our output clean.
    with contextlib.redirect_stdout(io.StringIO()):
        found = page.find_tables()
    for t in found.tables:
        rows = []
        for row in t.extract():
            rows.append([normalize_text(repair_calibri(c)[0])[0] if c is not None else None for c in row])
        cells = [[tuple(c) if c else None for c in r.cells] for r in t.rows]
        tables.append(RawTable(tuple(round(v, 2) for v in t.bbox), rows, cells))
    return tables


def extract_page(page: pymupdf.Page, calibri_fonts: tuple[str, ...] = ()) -> ExtractedPage:
    """`calibri_fonts`: extra font-name substrings to treat as Calibri for ligature repair, for
    PDFs whose embedded font names are anonymized (e.g. Microsoft Print to PDF's "CIDFont+F2")."""
    number = page.number + 1
    raw = page.get_text("rawdict")
    pieces = _pieces(raw, calibri_fonts)
    kept, dups = _split_duplicates(pieces)
    visual = _merge_visual_lines(kept)
    visual.sort(key=lambda p: (round(p.bbox[1], 1), p.bbox[0]))
    lines = [_to_line(p, number, i) for i, p in enumerate(visual)]
    lines += [_to_line(p, number, len(lines) + i, removed="duplicate_render") for i, p in enumerate(dups)]
    repairs: dict[str, int] = {}
    for p in visual:
        for k, v in p.repairs.items():
            repairs[k] = repairs.get(k, 0) + v
    return ExtractedPage(
        page_number=number,
        width=round(page.rect.width, 2),
        height=round(page.rect.height, 2),
        rotation=page.rotation,
        image_count=len(page.get_images()),
        raw_text=page.get_text("text"),
        lines=lines,
        tables=_find_tables(page),
        repairs=repairs,
    )


def open_pdf(path: Path) -> pymupdf.Document:
    return pymupdf.open(path)
