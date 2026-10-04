"""Mark noise lines as removed. Nothing is deleted; each rule records a reason.

Rules are deliberately narrow. A line is only removed when it is:
  - empty after normalization (e.g. an icon-font glyph),
  - inside the browser header/footer band of a web print (small text at the page edge),
  - a bare page number in the top/bottom margin,
  - identical text repeated at the same vertical position on at least half the pages
    (>= 3 pages), and located in the header/footer zone (top 12% / bottom 20% of the page;
    the bottom zone is larger because web cookie banners overlay the bottom of the page),
  - matched by an explicit per-document drop pattern from the overrides file.
Anything uncertain is left in place.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from .extract import ExtractedPage
from .profiles import Profile

BROWSER_BAND_TOP = 28.0  # pt from top edge
BROWSER_BAND_BOTTOM = 35.0  # pt from bottom edge
BROWSER_MAX_SIZE = 9.0
MARGIN_FRACTION = 0.15  # safe to be generous: numbers must also follow the page sequence
REPEAT_MIN_PAGES = 3
REPEAT_MIN_FRACTION = 0.5
REPEAT_Y_BUCKET = 4.0  # pt
REPEAT_TOP_ZONE = 0.12
REPEAT_BOTTOM_ZONE = 0.20

PAGE_NUMBER = re.compile(r"^(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?$", re.IGNORECASE)


def _alnum_count(text: str) -> int:
    return sum(ch.isalnum() for ch in text)


def mark_empty(pages: list[ExtractedPage]) -> None:
    for page in pages:
        for line in page.lines:
            if line.removed is None and not line.text:
                line.removed = "icon_or_empty"


def mark_browser_chrome(pages: list[ExtractedPage]) -> None:
    for page in pages:
        for line in page.lines:
            if line.removed or line.size > BROWSER_MAX_SIZE:
                continue
            if line.bbox[3] <= BROWSER_BAND_TOP or line.bbox[1] >= page.height - BROWSER_BAND_BOTTOM:
                line.removed = "browser_header_footer"


def mark_page_numbers(pages: list[ExtractedPage]) -> None:
    """Bare numbers in the margin are page numbers only if they follow the page sequence.

    A lone "4" at the top of page 2 can be a clause number (seen in this corpus), so we
    require the printed number to be a constant offset from the physical page number on
    at least two pages.
    """
    candidates = []
    for page in pages:
        margin = page.height * MARGIN_FRACTION
        for line in page.lines:
            m = PAGE_NUMBER.match(line.text) if not line.removed else None
            if m and (line.bbox[3] <= margin or line.bbox[1] >= page.height - margin):
                printed = int(re.search(r"\d+", line.text).group())
                candidates.append((printed - page.page_number, line))
    offsets = Counter(offset for offset, _ in candidates)
    for offset, line in candidates:
        if offsets[offset] >= 2:
            line.removed = "page_number"


def mark_repeated(pages: list[ExtractedPage]) -> None:
    """Running headers/footers and banners: same text, same vertical position, many pages."""
    n = len(pages)
    if n < REPEAT_MIN_PAGES:
        return
    threshold = max(REPEAT_MIN_PAGES, math.ceil(REPEAT_MIN_FRACTION * n))
    def in_zone(line, page) -> bool:
        return line.bbox[3] <= REPEAT_TOP_ZONE * page.height or line.bbox[1] >= (1 - REPEAT_BOTTOM_ZONE) * page.height

    seen: dict[tuple[str, int], set[int]] = defaultdict(set)
    for page in pages:
        for line in page.lines:
            if line.removed or not in_zone(line, page):
                continue
            seen[_repeat_key(line)].add(page.page_number)
    def pages_for(key: tuple[str, int]) -> int:
        text, bucket = key  # tolerate lines that straddle a bucket boundary
        return len(seen.get((text, bucket - 1), set()) | seen[key] | seen.get((text, bucket + 1), set()))

    for page in pages:
        for line in page.lines:
            if line.removed or not in_zone(line, page):
                continue
            key = _repeat_key(line)
            if pages_for(key) >= threshold and _alnum_count(line.text) >= 4:
                line.removed = "repeated_boilerplate"


def _repeat_key(line) -> tuple[str, int]:
    # Exact text: lines that differ only by a number are different content
    # (changing page numbers are handled by mark_page_numbers / browser chrome).
    return line.text.lower(), int(line.bbox[1] // REPEAT_Y_BUCKET)


def mark_override_patterns(pages: list[ExtractedPage], profile: Profile) -> None:
    if not profile.drop_line_patterns:
        return
    for page in pages:
        for line in page.lines:
            if line.removed is None and any(p.fullmatch(line.text) for p in profile.drop_line_patterns):
                line.removed = "override_pattern"


def clean_pages(pages: list[ExtractedPage], profile: Profile) -> dict[str, int]:
    mark_empty(pages)
    if profile.browser_chrome:
        mark_browser_chrome(pages)
    mark_page_numbers(pages)
    mark_repeated(pages)
    mark_override_patterns(pages, profile)
    counts: dict[str, int] = defaultdict(int)
    for page in pages:
        for line in page.lines:
            if line.removed:
                counts[line.removed] += 1
    return dict(counts)
