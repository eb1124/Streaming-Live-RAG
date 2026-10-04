"""Reading order via a conservative recursive XY-cut.

PDF content-stream order is unreliable here (Chrome prints paint positioned elements
out of order; e.g. a page title can come after the metadata grid). We order items by
geometry instead:

  1. If a region has a clear horizontal whitespace band (>= strong_gap), cut it into
     top-to-bottom bands. Paragraph spacing is normally the strongest signal.
  2. Otherwise, if it has a vertical whitespace gutter (>= min_v_gap), cut it into
     left-to-right columns (metadata grids, gutter numbers beside a paragraph).
  3. Otherwise sort by (top, left).

Preferring horizontal cuts keeps "1 | paragraph" gutter layouts together while still
reading multi-column grids column by column.
Limitation: a genuine two-column body with no strong horizontal gaps would be read
column-wise, which is correct; tables whose rows are far apart may be read row-wise.
"""

from __future__ import annotations

from typing import Callable, Sequence, TypeVar

T = TypeVar("T")


def _gaps(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Whitespace gaps (start, end) between the union of 1-D intervals."""
    intervals = sorted(intervals)
    gaps = []
    cur_end = intervals[0][1]
    for start, end in intervals[1:]:
        if start > cur_end:
            gaps.append((cur_end, start))
        cur_end = max(cur_end, end)
    return gaps


def xy_cut(
    items: Sequence[T],
    bbox: Callable[[T], tuple[float, float, float, float]],
    strong_gap: float,
    min_v_gap: float = 14.0,
    stats: dict | None = None,
) -> list[T]:
    stats = stats if stats is not None else {}
    if len(items) <= 1:
        return list(items)

    h_gaps = [g for g in _gaps([(bbox(i)[1], bbox(i)[3]) for i in items]) if g[1] - g[0] >= strong_gap]
    if h_gaps:
        return _split(items, bbox, h_gaps, axis=1, strong_gap=strong_gap, min_v_gap=min_v_gap, stats=stats)

    v_gaps = [g for g in _gaps([(bbox(i)[0], bbox(i)[2]) for i in items]) if g[1] - g[0] >= min_v_gap]
    if v_gaps:
        stats["vertical_cuts"] = stats.get("vertical_cuts", 0) + len(v_gaps)
        return _split(items, bbox, v_gaps, axis=0, strong_gap=strong_gap, min_v_gap=min_v_gap, stats=stats)

    return sorted(items, key=lambda i: (round(bbox(i)[1], 1), bbox(i)[0]))


def _split(items, bbox, gaps, axis, strong_gap, min_v_gap, stats):
    cuts = [(a + b) / 2 for a, b in gaps]
    groups: list[list] = [[] for _ in range(len(cuts) + 1)]
    for item in items:
        start = bbox(item)[axis]
        idx = sum(1 for c in cuts if start > c)
        groups[idx].append(item)
    ordered = []
    for group in groups:
        if group:
            ordered.extend(xy_cut(group, bbox, strong_gap, min_v_gap, stats))
    return ordered
