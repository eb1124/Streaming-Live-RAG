"""Group cleaned lines into blocks, classify them, and build the section tree.

Classification is conservative (see docs/ingestion.md):
  heading    font >= 1.15x body size, OR a fully bold short line when body text is not bold.
             Never from capitalisation alone. Max 3 lines / 25 words; no sentence-final punctuation.
             Runs of >= 3 consecutive same-style headings with nothing between are demoted to a
             list (tables of contents, link lists).
  list_item  explicit bullet glyph or enumerator (1. / 1.1 / (a) / ii.), or "implicit" (low
             confidence): CSS bullets with no glyph, detected from indentation after a ':' line
             or from runs of short single-line blocks at the same indent.
  table      PyMuPDF find_tables() candidates that pass sanity checks (>= 2x2, mostly filled,
             containing kept text). Lines inside an accepted table belong to the table block.
  paragraph  everything else.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

from .extract import ExtractedPage, RawTable
from .layout import xy_cut
from .models import Block, Document, Line, Section, TableData

BULLET_RE = re.compile(r"^([•◦▪▫●○■□‣⁃∙·*]|[-–](?=\s))\s*")
ORDERED_RES = [
    re.compile(r"^((\d{1,4}(?:\.\d{1,3})+)\.?)\s+"),  # 1.1  3.2.1.  4.4.3.1  5002.1
    re.compile(r"^((\d{1,3})[.)])\s+"),  # 1.  1)
    re.compile(r"^(\((\d{1,3})\))\s*"),  # (1)
    re.compile(r"^(\(?([ivxlc]{1,5})[.)])\s+"),  # i.  ii)  (iv)
    re.compile(r"^(\(?([a-z])[.)])\s+"),  # a.  b)  (c)
    re.compile(r"^(([A-Z]{1,3}\d{1,3}(?:\.\d{1,3})*)\.?)\s+"),  # PR1.  PR1.1.
]
SENTENCE_END = (".", ";", ",")
FUNCTION_WORDS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on", "or", "per",
    "than", "that", "the", "to", "under", "which", "with", "within", "without",
}
TABLE_MAX_PAGE_FRACTION = 0.6
KEY_VALUE_START = re.compile(r"^[A-Z][A-Za-z/&() -]{1,40}:\s")  # "Effective Date: ..." 
HEADING_SIZE_RATIO = 1.15
TABLE_MIN_FILLED = 0.5


@dataclass
class _Atom:
    bbox: tuple[float, float, float, float]
    line: Line | None = None
    table: RawTable | None = None
    table_lines: list[Line] = field(default_factory=list)


@dataclass
class DocStyle:
    body_size: float
    body_bold: bool
    line_width: float  # typical full line width of body text
    para_gap: float  # vertical gap above which a new paragraph starts


def match_marker(text: str) -> tuple[str, str, str | None] | None:
    """Return (list_type, marker, number) if text starts with a bullet or enumerator."""
    m = BULLET_RE.match(text)
    if m and len(text) > len(m.group(0)):
        return "bullet", m.group(1), None
    for rx in ORDERED_RES:
        m = rx.match(text)
        if m and len(text) > len(m.group(0)):
            return "ordered", m.group(1), m.group(2)
    return None


def _kept(lines: list[Line]) -> list[Line]:
    return [ln for ln in lines if not ln.removed]


def compute_style(pages: list[ExtractedPage]) -> DocStyle:
    sizes: Counter = Counter()
    bold: Counter = Counter()
    for page in pages:
        for ln in _kept(page.lines):
            key = round(ln.size * 2) / 2
            sizes[key] += len(ln.text)
            bold[key] += ln.bold_ratio * len(ln.text)
    if not sizes:
        return DocStyle(11.0, False, 400.0, 8.0)
    body = sizes.most_common(1)[0][0]
    body_bold = bold[body] / sizes[body] > 0.5
    widths = sorted(
        ln.bbox[2] - ln.bbox[0]
        for page in pages
        for ln in _kept(page.lines)
        if abs(ln.size - body) <= 0.6
    )
    line_width = widths[int(0.9 * (len(widths) - 1))] if widths else 400.0
    return DocStyle(body, body_bold, line_width, para_gap=0.0)


def _learn_para_gap(ordered_pages: list[list[_Atom]], style: DocStyle) -> float:
    """Typical gap between consecutive lines of one paragraph (mode), plus half a body size."""
    gaps = []
    for atoms in ordered_pages:
        lines = [a.line for a in atoms if a.line is not None]
        for prev, cur in zip(lines, lines[1:]):
            if abs(prev.size - style.body_size) > 0.6 or abs(cur.size - prev.size) > 0.3:
                continue
            if abs(cur.bbox[0] - prev.bbox[0]) > 2:
                continue
            gap = cur.bbox[1] - prev.bbox[3]
            if -2 <= gap <= 2 * style.body_size:
                gaps.append(round(gap))
    typical = statistics.mode(gaps) if gaps else 0.3 * style.body_size
    return typical + 0.5 * style.body_size


def _inside(ln: Line, box, tol: float = 1.0) -> bool:
    cx = (ln.bbox[0] + ln.bbox[2]) / 2
    cy = (ln.bbox[1] + ln.bbox[3]) / 2
    return box[0] - tol <= cx <= box[2] + tol and box[1] - tol <= cy <= box[3] + tol


def table_rows(table: RawTable, lines: list[Line]) -> list[list[str]]:
    """Cell text rebuilt from our (repaired) lines, with split sub-columns merged.

    PyMuPDF sometimes splits one logical column into several (spanning header cells are
    reported as None). Logical columns are taken from the first row: each non-None cell
    starts a column group that extends over the following None cells.
    """
    kept = [ln for ln in lines if not ln.removed]
    boxes = [b for row in table.cells for b in row if b]
    # If one of our visual lines straddles cell borders, our lines cannot be split per cell:
    # use PyMuPDF's own cell text for this table instead (Calibri repair already applied).
    straddles = any(
        _inside(ln, b) and (ln.bbox[0] < b[0] - 2 or ln.bbox[2] > b[2] + 2) for ln in kept for b in boxes
    )
    texts: list[list[str | None]] = []
    for r_idx, row in enumerate(table.cells):
        out = []
        for c_idx, box in enumerate(row):
            if box is None:
                out.append(None)
                continue
            fallback = table.rows[r_idx][c_idx] if c_idx < len(table.rows[r_idx]) else None
            if straddles:
                out.append(fallback or "")
                continue
            inside = sorted((ln for ln in kept if _inside(ln, box)), key=lambda ln: (ln.bbox[1], ln.bbox[0]))
            out.append(_join(inside) if inside else (fallback or ""))
        texts.append(out)
    if not texts:
        return []
    header = texts[0]
    starts = [i for i, c in enumerate(header) if c is not None] or [0]
    bounds = list(zip(starts, starts[1:] + [len(header)]))
    return [[" ".join(c for c in row[a:b] if c).strip() for a, b in bounds] for row in texts]


def _accept_table(table: RawTable, lines: list[Line], page_area: float) -> tuple[bool, list[Line], str]:
    n_rows = len(table.cells)
    n_cols = max((len(r) for r in table.cells), default=0)
    if n_rows < 2 or n_cols < 2:
        return False, [], f"{n_rows}x{n_cols} too small"
    x0, y0, x1, y1 = table.bbox
    if (x1 - x0) * (y1 - y0) > TABLE_MAX_PAGE_FRACTION * page_area:
        return False, [], "covers most of the page (layout region, not a table)"
    real = [c for r in table.rows for c in r if c is not None]  # None = spanned
    filled = sum(1 for c in real if c.strip())
    if filled / max(len(real), 1) < TABLE_MIN_FILLED:
        return False, [], f"{filled}/{len(real)} cells filled"
    multi_value_rows = sum(1 for r in table.rows if sum(1 for c in r if c and c.strip()) >= 2)
    if multi_value_rows < 2:
        return False, [], "fewer than 2 rows with 2+ values"
    inside = []
    for ln in lines:
        cx = (ln.bbox[0] + ln.bbox[2]) / 2
        cy = (ln.bbox[1] + ln.bbox[3]) / 2
        if x0 - 1 <= cx <= x1 + 1 and y0 - 1 <= cy <= y1 + 1:
            inside.append(ln)
    kept_inside = [ln for ln in inside if not ln.removed]
    if not kept_inside:
        return False, [], "covers only removed/noise text"
    if len(kept_inside) < 0.5 * len(inside):
        return False, [], "mostly covers removed/noise text"
    return True, kept_inside, "ok"


def order_page(page: ExtractedPage, style: DocStyle, doc: Document) -> list[_Atom]:
    atoms: list[_Atom] = []
    claimed: set[str] = set()
    for table in page.tables:
        ok, inside, reason = _accept_table(table, page.lines, page.width * page.height)
        inside = [ln for ln in inside if ln.line_id not in claimed]
        if not ok or not inside:
            doc.warn("table_rejected", f"table candidate rejected ({reason})", "info", page.page_number)
            continue
        claimed.update(ln.line_id for ln in inside)
        table.rows = table_rows(table, page.lines)
        atoms.append(_Atom(table.bbox, table=table, table_lines=inside))
    for ln in _kept(page.lines):
        if ln.line_id not in claimed:
            atoms.append(_Atom(ln.bbox, line=ln))
    stats: dict = {}
    ordered = xy_cut(atoms, lambda a: a.bbox, strong_gap=1.1 * style.body_size, stats=stats)
    if stats.get("vertical_cuts"):
        doc.warn(
            "columnar_layout",
            f"{stats['vertical_cuts']} vertical cut(s): side-by-side text read column by column",
            "info",
            page.page_number,
        )
    return ordered


def _is_continuation(prev: Line, cur: Line) -> bool:
    """Does `cur` continue the sentence in `prev`?"""
    if not cur.text:
        return False
    if prev.text.endswith((".", ":", ";", "?", "!")):
        return False
    if cur.text[0].islower() or cur.text[0] in "([$":
        return True
    if prev.text.count("(") > prev.text.count(")"):
        return True  # an open parenthesis cannot end a sentence: "... Administration (U. T. System" + "Administration) - ..."
    last = prev.text.rsplit(" ", 1)[-1].lower()
    return last in FUNCTION_WORDS or prev.text.endswith((",", "-", "/", "&"))


# A clause number printed alone on its line, its text starting on the next line ("PR6.1." above
# "Receipts/supporting documentation are required ..."). Multi-level or prefixed numbers only: a bare
# single number is a gutter number, attached to the text beside it by attach_gutter_numbers.
BARE_ENUMERATOR = re.compile(r"^(?:[A-Z]{1,3}\d{1,3}(?:\.\d{1,3})*|\d{1,4}(?:\.\d{1,3})+)\.?$")
WRAP_WIDTH = 0.75  # of the typical body line width


def _wrapped(prev: Line, cur: Line, style: DocStyle, same_style: bool = True) -> bool:
    """`prev` is a long line that stops mid-sentence and `cur` is set in the same style directly
    below it: the text wrapped. Such a pair is never split by indentation or a style switch
    (Rutgers' bold note "... University Policy 40.4.1: Travel and Business" / "Expense Policy.")."""
    return (
        (prev.bbox[2] - prev.bbox[0]) >= WRAP_WIDTH * style.line_width
        and not prev.text.endswith((".", ":", ";", "?", "!"))
        and (not same_style or _same_style(prev, cur))
    )


def _same_style(prev: Line, cur: Line) -> bool:
    return (
        abs(prev.size - cur.size) <= 0.3
        and (prev.bold_ratio >= 0.9) == (cur.bold_ratio >= 0.9)
        and prev.font == cur.font
    )


def _should_break(prev: Line, cur: Line, block_lines: list[Line], style: DocStyle) -> bool:
    if BARE_ENUMERATOR.match(cur.text) and not _wrapped(prev, cur, style, same_style=False):
        return True  # (after a wrapped line it is a cross-reference ending the sentence: "... Policy" / "P3.6.")
    if (
        len(block_lines) == 1
        and BARE_ENUMERATOR.match(prev.text)
        and cur.bbox[1] > (prev.bbox[1] + prev.bbox[3]) / 2  # below it, not beside it
        and abs(cur.bbox[0] - prev.bbox[0]) <= 3  # the clause text starts at the number's indent
    ):
        return False  # the enumerator's text starts on the next line
    if match_marker(cur.text):
        return True
    gap = cur.bbox[1] - prev.bbox[3]
    # Large fonts have tall boxes that overlap the next line, so allow some negative gap.
    if gap > max(style.para_gap, 0.5 * max(prev.size, cur.size)) or gap < -0.5 * max(prev.size, cur.size):
        return True
    if abs(cur.size - prev.size) > 0.15 * max(cur.size, prev.size):
        return True
    continuation = _is_continuation(prev, cur)
    if KEY_VALUE_START.match(cur.text) and not continuation:
        return True
    if prev.bold_ratio >= 0.9 and cur.bold_ratio < 0.5 and not continuation:
        return True
    if cur.bold_ratio >= 0.9 and prev.bold_ratio < 0.5 and not continuation:
        return True
    starts_with_marker = match_marker(block_lines[0].text) is not None
    # Continuation lines of a numbered item hang under its text, which starts after the
    # enumerator: "5.1.1. When purchasing ..." continues ~36pt to the right at 12pt.
    marker = match_marker(block_lines[0].text) if starts_with_marker else None
    hang = max(30.0, 0.6 * block_lines[0].size * len(marker[1]) + 12) if marker else 15.0
    if cur.bbox[0] > prev.bbox[0] + hang and not _wrapped(prev, cur, style):
        return True
    if len(block_lines) >= 2 and cur.bbox[0] < prev.bbox[0] - 3:
        return True
    # A short body line ends its paragraph (only the last line of a paragraph is short).
    # Not applied to larger text: multi-line headings are short on every line.
    body_sized = prev.size <= 1.1 * style.body_size
    prev_short = (prev.bbox[2] - prev.bbox[0]) < 0.6 * style.line_width
    if body_sized and prev_short and not continuation:
        return True
    return False


def _join(lines: list[Line]) -> str:
    out = ""
    for ln in lines:
        t = ln.text
        if not out:
            out = t
        elif out.endswith("-") and len(out) > 1 and out[-2].isalpha() and t[:1].isalpha():
            out += t  # keep the hyphen: "P-" + "Card" -> "P-Card"
        elif re.search(r"(https?://|www\.)\S*$", out) and not out.endswith((".", ")")):
            out += t  # URL wrapped across lines
        else:
            out += " " + t
    return out


def _union(boxes) -> tuple[float, float, float, float]:
    boxes = list(boxes)
    return (
        round(min(b[0] for b in boxes), 2),
        round(min(b[1] for b in boxes), 2),
        round(max(b[2] for b in boxes), 2),
        round(max(b[3] for b in boxes), 2),
    )


def _weighted(lines: list[Line], attr: str) -> float:
    total = sum(len(ln.text) for ln in lines) or 1
    return sum(getattr(ln, attr) * len(ln.text) for ln in lines) / total


def build_blocks(page_number: int, atoms: list[_Atom], style: DocStyle) -> list[Block]:
    blocks: list[Block] = []
    current: list[Line] = []

    def flush():
        if not current:
            return
        size = max(Counter(round(ln.size, 1) for ln in current).items(), key=lambda kv: (kv[1], kv[0]))[0]
        block = Block(
            block_id=f"p{page_number}-b{len(blocks)}",
            page=page_number,
            kind="paragraph",
            text=_join(current),
            bbox=_union(ln.bbox for ln in current),
            line_ids=[ln.line_id for ln in current],
            size=size,
            bold_ratio=round(_weighted(current, "bold_ratio"), 2),
            line_count=len(current),
        )
        blocks.append(block)
        current.clear()

    for atom in atoms:
        if atom.table is not None:
            flush()
            rows = atom.table.rows
            text = "\n".join(" | ".join(c or "" for c in row) for row in rows)
            blocks.append(
                Block(
                    block_id=f"p{page_number}-b{len(blocks)}",
                    page=page_number,
                    kind="table",
                    text=text,
                    bbox=atom.table.bbox,
                    line_ids=[ln.line_id for ln in atom.table_lines],
                    size=round(statistics.median(ln.size for ln in atom.table_lines), 1),
                    table=TableData(rows=rows),
                    confidence="medium",
                )
            )
            continue
        ln = atom.line
        if current and _should_break(current[-1], ln, current, style):
            flush()
        current.append(ln)
    flush()
    return blocks


def classify_blocks(blocks: list[Block], style: DocStyle) -> None:
    for block in blocks:
        if block.kind == "table":
            continue
        text = block.text
        bold = block.bold_ratio
        n_lines = block.line_count
        marker = match_marker(text)
        ratio = block.size / style.body_size if style.body_size else 1.0
        words = len(text.split())
        headingish = (
            n_lines <= 3
            and len(text) <= 160
            and words <= 25
            and not text.endswith(SENTENCE_END)
            and sum(ch.isalpha() for ch in text) >= 2
            and not (marker and marker[0] == "bullet")
        )
        if headingish and ratio >= HEADING_SIZE_RATIO:
            block.kind = "heading"
            block.confidence = "high" if ratio >= 1.3 else "medium"
        elif (
            headingish
            and bold >= 0.9
            and not style.body_bold
            and n_lines <= 2
            and len(text) <= 120
            and not text.endswith(":")
        ):
            block.kind = "heading"
            block.confidence = "medium"
        elif marker:
            block.kind = "list_item"
            block.list_type = marker[0]
        if marker and block.kind in ("heading", "list_item"):
            block.marker = marker[1]
            block.number = marker[2]


GUTTER_NUMBER = re.compile(r"^\(?(\d{1,3}|[a-z])[.)]?$")


def attach_gutter_numbers(blocks: list[Block]) -> list[Block]:
    """A bare enumerator printed in a left gutter beside a paragraph ("5 | Final approval ...")
    becomes that paragraph's list marker, so the number stays with the clause it labels."""
    out: list[Block] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        m = GUTTER_NUMBER.match(b.text)
        if (
            m
            and b.kind == "paragraph"
            and nxt is not None
            and nxt.kind in ("paragraph", "list_item")
            and nxt.page == b.page
            and nxt.bbox[0] > b.bbox[2]
            and nxt.bbox[1] - 5 <= b.bbox[1] <= nxt.bbox[3]
        ):
            nxt.text = f"{b.text} {nxt.text}"
            nxt.line_ids = b.line_ids + nxt.line_ids
            nxt.bbox = _union([b.bbox, nxt.bbox])
            nxt.kind, nxt.list_type, nxt.marker, nxt.number = "list_item", "ordered", b.text, m.group(1)
            nxt.flags.append("gutter_number_attached")
            out.append(nxt)
            i += 2
            continue
        out.append(b)
        i += 1
    for idx, b in enumerate(out):  # keep block ids contiguous per page
        b.block_id = f"p{b.page}-b{idx}"
    return out


def _style_key(block: Block) -> tuple[float, bool]:
    return round(block.size * 2) / 2, block.bold_ratio >= 0.9


def demote_heading_runs(blocks: list[Block]) -> None:
    """>= 3 consecutive same-style headings with nothing between = a TOC or link list, not headings."""
    i = 0
    while i < len(blocks):
        j = i
        while (
            j < len(blocks)
            and blocks[j].kind == "heading"
            and _style_key(blocks[j]) == _style_key(blocks[i])
        ):
            j += 1
        if j - i >= 3:
            for b in blocks[i:j]:
                b.kind = "list_item"
                b.list_type = "ordered" if b.number else "implicit"
                b.confidence = "low"
                b.flags.append("heading_run_demoted")
        i = max(j, i + 1)


def detect_implicit_lists(blocks: list[Block], style: DocStyle) -> None:
    """Lists whose bullets are CSS-drawn (no glyph in the PDF). Low confidence by design."""
    # (a) indented blocks following a line that ends with ':'
    for idx in range(1, len(blocks)):
        prev, cur = blocks[idx - 1], blocks[idx]
        if cur.kind != "paragraph":
            continue
        intro = prev.kind == "paragraph" and prev.text.endswith(":")
        sibling = prev.list_type == "implicit" and abs(prev.bbox[0] - cur.bbox[0]) <= 2
        indent = cur.bbox[0] - (prev.bbox[0] if intro else cur.bbox[0])
        if (intro and 4 <= indent <= 40) or sibling:
            cur.kind = "list_item"
            cur.list_type = "implicit"
            cur.confidence = "low"
    # (b) runs of >= 3 short single-line paragraphs at the same x (TOCs, link lists, menus)
    i = 0
    while i < len(blocks):
        j = i
        while (
            j < len(blocks)
            and blocks[j].kind == "paragraph"
            and blocks[j].line_count == 1
            and len(blocks[j].text.split()) <= 10
            and not blocks[j].text.endswith(".")
            and abs(blocks[j].bbox[0] - blocks[i].bbox[0]) <= 2
        ):
            j += 1
        if j - i >= 3:
            for b in blocks[i:j]:
                b.kind = "list_item"
                b.list_type = "implicit"
                b.confidence = "low"
        i = max(j, i + 1)


SITE_FOOTER_MARKER = re.compile(r"(©|\bcopyright\b|\bprivacy (policy|notice|statement)\b)", re.IGNORECASE)
CHROME_HEADER_PAGES = 2
FOOTER_MAX_WORDS = 20


def flag_site_chrome(doc: Document, title: str | None) -> None:
    """Web prints: flag (never remove) site navigation before the page title and the site footer.

    Header: blocks before the first block matching the title, searched on the first two pages
    (site navigation can push the title to page 2).
    Footer: walking back from the last block, the trailing run of short, sentence-less blocks
    (menus, link lists, contact lines). It must contain a copyright/privacy line to count as a
    site footer, and stops at the first real sentence or table, so the policy's own closing
    sections (references, history, roles) are not flagged.
    """
    blocks = list(doc.iter_blocks())
    if not blocks:
        return
    if title:
        norm_title = _norm(title)
        first_pages = {p.page_number for p in doc.pages[:CHROME_HEADER_PAGES]}
        head = [b for b in blocks if b.page in first_pages]
        normed = [_norm(b.text) for b in head]
        # Exact match first: a breadcrumb like "HOME / ... / <TITLE>" also contains the title.
        idx = next((i for i, nb in enumerate(normed) if nb == norm_title), None)
        if idx is None:
            idx = next(
                (i for i, nb in enumerate(normed) if len(nb) >= 5 and (nb in norm_title or norm_title in nb)), None
            )
        for chrome in head[: idx or 0]:
            chrome.flags.append("suspected_chrome:header")
        _flag_breadcrumbs(head)

    start = len(blocks)
    while start > 0:
        b = blocks[start - 1]
        text = b.text.strip()
        is_sentence = text.endswith((".", "?", "!")) and not SITE_FOOTER_MARKER.search(text)
        if b.kind == "table" or len(text.split()) >= FOOTER_MAX_WORDS or is_sentence:
            break
        start -= 1
    tail = blocks[start:]
    if any(SITE_FOOTER_MARKER.search(b.text) for b in tail):
        for b in tail:
            b.flags.append("suspected_chrome:footer")


BREADCRUMB_MAX_WORDS = 10


def _flag_breadcrumbs(blocks: list[Block]) -> None:
    """A breadcrumb trail ("Home | ISO Policies, Standards, and Guidelines | <page title>"): short
    blocks side by side on one row, the first reading "Home". It can sit below the page title,
    where the header rule above does not reach."""
    for i, first in enumerate(blocks):
        if _norm(first.text) != "home" or any(f.startswith("suspected_chrome") for f in first.flags):
            continue
        row = [first]
        for b in blocks[i + 1:]:
            same_row = b.page == first.page and min(b.bbox[3], first.bbox[3]) - max(b.bbox[1], first.bbox[1]) > 0
            if not same_row or b.bbox[0] <= row[-1].bbox[0] or len(b.text.split()) > BREADCRUMB_MAX_WORDS:
                break
            row.append(b)
        if len(row) >= 2:
            for b in row:
                b.flags.append("suspected_chrome:breadcrumb")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def is_chrome(block: Block) -> bool:
    return any(f.startswith("suspected_chrome") for f in block.flags)


def is_retrievable(block: Block) -> bool:
    """Whether a later chunker should index this block. Excluded blocks stay in the JSON."""
    return not is_chrome(block) and not any(f.startswith("exclude_from_retrieval") for f in block.flags)


def exclude_sections(doc: Document, rules: list[dict]) -> int:
    """Flag every block under a section whose title matches `rules[i]["title"]` (case-insensitive).

    Used for content that is kept in the normalized representation but is unreliable or out of
    scope for Phase 1 retrieval (e.g. a revision-history table read cell by cell).
    """
    by_id = {s.section_id: s for s in doc.sections}
    count = 0
    for rule in rules:
        target = rule["title"].strip().lower()
        for b in doc.iter_blocks():
            sid = b.section_id
            while sid and by_id[sid].title.strip().lower() != target:
                sid = by_id[sid].parent_id
            if sid:
                b.flags.append(f"exclude_from_retrieval:{rule.get('reason', 'curated')}")
                b.confidence = "low"
                count += 1
        if not any(s.title.strip().lower() == target for s in doc.sections):
            doc.warn("override_rejected", f"exclude section {rule['title']!r}: no such section")
    return count


def assign_levels_and_sections(doc: Document) -> None:
    blocks = list(doc.iter_blocks())
    headings = [b for b in blocks if b.kind == "heading" and not is_chrome(b)]

    # Level = rank of the heading's visual style: larger first, then bold, then ALL CAPS.
    # Capitalisation only orders headings that were already detected; it never creates one.
    def level_key(b: Block) -> tuple[float, bool, bool]:
        letters = [ch for ch in b.text if ch.isalpha()]
        upper = len(letters) >= 3 and all(ch.isupper() for ch in letters)
        return (*_style_key(b), upper)

    styles = sorted({level_key(b) for b in headings}, key=lambda s: (-s[0], not s[1], not s[2]))
    level_of = {s: min(i + 1, 6) for i, s in enumerate(styles)}
    stack: list[Section] = []
    sections: list[Section] = []
    for b in blocks:
        if b.kind == "heading" and not is_chrome(b):
            level = level_of[level_key(b)]
            b.heading_level = level
            while stack and stack[-1].level >= level:
                stack.pop()
            sec = Section(
                section_id=f"s{len(sections)}",
                title=b.text,
                level=level,
                number=b.number,
                parent_id=stack[-1].section_id if stack else None,
                page_start=b.page,
                heading_block_id=b.block_id,
            )
            sections.append(sec)
            stack.append(sec)
        b.section_id = stack[-1].section_id if stack else None
    doc.sections = sections


def section_path(doc: Document, section_id: str | None) -> list[str]:
    by_id = {s.section_id: s for s in doc.sections}
    path = []
    while section_id:
        sec = by_id[section_id]
        path.append(sec.title)
        section_id = sec.parent_id
    return path[::-1]
