"""Temporal intent parsing and version resolution (synthetic metadata; no retrieval models)."""

from datetime import date
from types import SimpleNamespace

import pytest

from temporal.intent import parse_query
from temporal.resolve import TemporalResolver
from temporal.versions import Version, VersionRegistry


@pytest.mark.parametrize("query,kind,dates", [
    ("Under the procedures effective July 1, 2026, can a card be used?", "point_in_time", [(2026, 7, 1)]),
    ("What did the February 2026 procedures say?", "point_in_time", [(2026, 2, None)]),
    ("Rules on Sept. 3rd, 2026?", "point_in_time", [(2026, 9, 3)]),
    ("As of 2026-03-15 what applied?", "point_in_time", [(2026, 3, 15)]),
    ("As of 3/15/2026 what applied?", "point_in_time", [(2026, 3, 15)]),
    ("How did the rule change between February 2026 and July 2026?", "compare", [(2026, 2, None), (2026, 7, None)]),
    ("What is UConn's current rule on card suspension?", "current", []),
    ("What are the latest UConn travel procedures on tickets?", "current", []),
    ("Under the procedures effective July 1, 2026 what is the current rule?", "point_in_time", [(2026, 7, 1)]),
    ("What trip cost needs SIO approval?", "neutral", []),
    ("What did UConn require in 2026?", "neutral", []),  # a bare year is too coarse
    ("Travelers may book airfare early", "neutral", []),  # 'may' without a year is not a date
])
def test_parse_query(query, kind, dates):
    intent = parse_query(query)
    assert intent.kind == kind
    assert [(d.year, d.month, d.day) for d in intent.dates] == dates


FEB = Version("proc-feb", "series-x", date(2026, 2, 1), date(2026, 7, 1), False)
JUL = Version("proc-jul", "series-x", date(2026, 7, 1), None, True)
SOLO = Version("policy", "series-solo", date(2026, 7, 1), None, True)  # single-version series: never resolved


def resolver():
    reg = VersionRegistry([JUL, FEB, SOLO])
    doc_of = {"f1": "proc-feb", "f2": "proc-feb", "j1": "proc-jul", "j2": "proc-jul", "p1": "policy", "o1": "oregon"}
    return TemporalResolver(reg, doc_of)


RANKED = ["f1", "p1", "j1", "o1", "f2", "j2"]


def test_registry_uses_only_multi_version_series():
    reg = VersionRegistry([JUL, FEB, SOLO])
    assert list(reg.by_series) == ["series-x"] and [v.doc_id for v in reg.by_series["series-x"]] == ["proc-feb", "proc-jul"]
    assert "policy" not in reg.series_of


@pytest.mark.parametrize("query,kept,selected", [
    ("Under the procedures effective July 1, 2026, ...", ["p1", "j1", "o1", "j2"], ["proc-jul"]),
    ("Under the February 1, 2026 procedures ...", ["f1", "p1", "o1", "f2"], ["proc-feb"]),
    ("What applied on June 30, 2026?", ["f1", "p1", "o1", "f2"], ["proc-feb"]),  # superseded_date is exclusive
    ("What applied as of September 2026?", ["p1", "j1", "o1", "j2"], ["proc-jul"]),  # month not naming a version: in force on the 1st
    ("What is the current rule?", ["p1", "j1", "o1", "j2"], ["proc-jul"]),
    ("Compare February 2026 and July 2026", RANKED, ["proc-feb", "proc-jul"]),
])
def test_resolution_keeps_selected_version_in_stable_order(query, kept, selected):
    res = resolver().resolve(query, RANKED)
    assert res.kept == kept and res.selected == {"series-x": selected}
    assert set(res.kept) | set(res.dropped) == set(RANKED)


def test_neutral_query_is_unchanged():
    res = resolver().resolve("What trip cost needs SIO approval?", RANKED)
    assert res.kept == RANKED and res.dropped == [] and res.selected == {} and not res.changed


def test_date_before_any_version_removes_the_series_and_flags_it():
    res = resolver().resolve("What were the procedures effective January 1, 2025?", RANKED)
    assert res.kept == ["p1", "o1"] and res.selected == {"series-x": []}
    assert res.flags == ["requested_version_unavailable:series-x"]


def test_ambiguous_current_version_is_not_filtered():
    both_current = VersionRegistry([Version("a", "s", date(2026, 1, 1), None, True), Version("b", "s", date(2026, 2, 1), None, True)])
    r = TemporalResolver(both_current, {"x": "a", "y": "b"})
    res = r.resolve("current rule?", ["x", "y"])
    assert res.kept == ["x", "y"] and res.flags == ["current_version_ambiguous:s"]


def test_registry_from_chunk_metadata_only():
    chunks = [SimpleNamespace(chunk_id="c1", doc_id="d1", series_id="s", effective_date="2026-02-01", superseded_date="2026-07-01", is_current=False),
              SimpleNamespace(chunk_id="c2", doc_id="d2", series_id="s", effective_date="2026-07-01", superseded_date=None, is_current=True),
              SimpleNamespace(chunk_id="c3", doc_id="d3", series_id=None, effective_date="2026-07-01", superseded_date=None, is_current=True),
              SimpleNamespace(chunk_id="c4", doc_id="d4", series_id="t", effective_date="1990-09", superseded_date=None, is_current=None)]
    r = TemporalResolver.from_chunks(chunks)
    assert list(r.registry.by_series) == ["s"]  # 't' has one version; d3 has no series
    assert r.resolve("current?", ["c1", "c2", "c3", "c4"]).kept == ["c2", "c3", "c4"]


def test_resolution_is_deterministic():
    a, b = resolver().resolve("current rule?", RANKED), resolver().resolve("current rule?", RANKED)
    assert (a.kept, a.dropped, a.selected, a.flags) == (b.kept, b.dropped, b.selected, b.flags)


def test_temporal_suite_is_well_formed():
    from chunking.config import CHUNKS_DIR
    from chunking.pipeline import load_chunks
    from evaluation.temporal.cases import CASES, resolve_ref

    cats = {c["category"] for c in CASES}
    assert {"explicit_july", "explicit_february", "current", "neutral", "conflict", "one_version_only"} <= cats
    if not (CHUNKS_DIR / "_manifest.json").exists():
        pytest.skip("chunks not generated")
    cb = load_chunks()
    for c in CASES:
        assert parse_query(c["query"]).kind == c["intent"]
        for r in [r for u in c["gold"] for r in u] + c["other"]:
            assert resolve_ref(r, cb)
