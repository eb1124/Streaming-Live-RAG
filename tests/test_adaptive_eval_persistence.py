"""Adaptive evaluation harness (offline): case-by-case persistence, incomplete runs, split diagnostics.

No retrieval models and no network: run_case is replaced by a scripted fake; split diagnostics use the
controller with the fake stack and StubProvider of tests/test_adaptive_controller.py.
"""

import json
from pathlib import Path

import pytest

import evaluation.adaptive.run as ar
from adaptive.controller import AdaptiveController
from generation.providers import StubProvider
from tests.test_adaptive_controller import FEB_CARD, JUL_CARD, OTHER, card_script, insufficient, stack
from tests.test_generation_verification import FEB_Q, NEUTRAL_Q

CASES = [{"id": f"c{i}"} for i in range(1, 4)]
META = {"provider": "stub", "model": "stub-1", "prompt_version": "test"}


def record(case_id, passed=True, strategy="single"):
    return {"id": case_id, "expect": "answer", "provider_calls": [], "split_diagnostics": None,
            "answer": {"abstention_reason": None, "decision": {"strategy": strategy, "reason": "r"}},
            "checks": {"passed": passed, "status": "answer", "abstention_reason": None, "gold": [], "failures": []}}


class Interrupted(Exception):
    pass


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "RESULTS", tmp_path)
    monkeypatch.setattr(ar, "BASELINE", tmp_path / "no-baseline")
    monkeypatch.setattr(ar, "CASES", CASES)
    return tmp_path


def saved(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_each_completed_case_is_saved_before_the_next_case_runs(harness, monkeypatch):
    seen_on_disk = []

    def fake_run_case(case, controller, provider, env):
        path = harness / "run-1.json"
        seen_on_disk.append([r["id"] for r in saved(path)["cases"]] if path.exists() else None)
        return record(case["id"])

    monkeypatch.setattr(ar, "run_case", fake_run_case)
    run = ar.run_once(1, None, None, None)
    assert seen_on_disk == [None, ["c1"], ["c1", "c2"]]
    data = saved(harness / "run-1.json")
    assert data["complete"] is True and run["complete"] is True
    assert [r["id"] for r in data["cases"]] == ["c1", "c2", "c3"] and data["cases_total"] == 3
    assert data["started"] == run["started"] and data["cases"] == run["cases"]
    assert not list(harness.glob("*.tmp"))


def test_interrupted_run_keeps_completed_cases_marked_incomplete(harness, monkeypatch):
    def fake_run_case(case, controller, provider, env):
        if case["id"] == "c3":
            raise Interrupted("rate limited / stopped")
        return record(case["id"], passed=case["id"] == "c1")

    monkeypatch.setattr(ar, "run_case", fake_run_case)
    with pytest.raises(Interrupted):
        ar.run_once(2, None, None, None)
    data = saved(harness / "run-2.json")
    assert data["complete"] is False and [r["id"] for r in data["cases"]] == ["c1", "c2"]
    assert data["cases"][0]["checks"]["passed"] is True and data["cases"][1]["checks"]["passed"] is False  # unchanged
    assert not list(harness.glob("*.tmp"))


def test_save_is_atomic_a_failed_write_leaves_the_previous_file(harness, monkeypatch):
    path = harness / "run-1.json"
    ar.save_run(path, "t0", [record("c1")], complete=False)
    before = path.read_text(encoding="utf-8")
    real = Path.write_text

    def failing(self, *a, **kw):
        if self.name.endswith(".tmp"):
            real(self, '{"trunc', encoding="utf-8")
            raise OSError("disk full")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", failing)
    with pytest.raises(OSError):
        ar.save_run(path, "t0", [record("c1"), record("c2")], complete=False)
    assert path.read_text(encoding="utf-8") == before and saved(path)["cases"][0]["id"] == "c1"


def test_summary_of_an_incomplete_run_reports_no_run_total_and_no_agreement(harness):
    complete = {"started": "t1", "complete": True, "cases": [record("c1"), record("c2"), record("c3", passed=False)]}
    partial = {"started": "t2", "complete": False, "cases": [record("c1"), record("c2", passed=False)]}
    report = ar.write_summary([complete, partial], META)
    assert "## Run 1: 2/3 passed" in report
    assert "## Run 2: INCOMPLETE, 2 of 3 cases completed; 1/2 completed cases passed (no run total)" in report
    assert "## Run 2: 1/3" not in report and "Not computed: at least one run is incomplete." in report
    assert (harness / "summary.md").read_text(encoding="utf-8") == report


def test_load_runs_reads_saved_files_in_order_including_older_files_without_the_flag(harness):
    ar.save_run(harness / "run-1.json", "t1", [record(c["id"]) for c in CASES], complete=True)
    (harness / "run-2.json").write_text(json.dumps({"started": "t2", "cases": [record("c1")]}), encoding="utf-8")
    runs = ar.load_runs()
    assert [r["started"] for r in runs] == ["t1", "t2"] and [r["complete"] for r in runs] == [True, False]


def test_summarize_cli_rewrites_the_summary_without_calling_a_model(harness):
    ar.save_run(harness / "run-1.json", "t1", [record("c1"), record("c2")], complete=False)
    assert ar.main(["--summarize"]) == 0
    assert "INCOMPLETE, 2 of 3 cases completed" in (harness / "summary.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- split diagnostics


def run_split(**script):
    s = stack(JUL_CARD, FEB_CARD, OTHER)
    return AdaptiveController(s, StubProvider(card_script(**script))).run(NEUTRAL_Q).to_dict()


def test_split_diagnostics_record_each_part_and_its_citations():
    d = ar.split_diagnostics(run_split())
    assert d["final_status"] == "answered" and (d["parts_answered"], d["parts_abstained"]) == (2, 0)
    assert [p["doc_id"] for p in d["parts"]] == ["proc-feb", "proc-jul"]
    assert [p["cited_chunk_ids"] for p in d["parts"]] == [[FEB_CARD.chunk_id], [JUL_CARD.chunk_id]]
    assert [p["citations"][0]["label"] for p in d["parts"]] == ["S1", "S1"]  # each part's own context label
    assert d["final_cited_chunk_ids"] == [FEB_CARD.chunk_id, JUL_CARD.chunk_id]
    assert d["every_part_citation_retained"] and all(p["retained_in_final"] for p in d["parts"])
    assert JUL_CARD.chunk_id not in d["parts"][0]["sources_shown"] and FEB_CARD.chunk_id not in d["parts"][1]["sources_shown"]


def test_split_diagnostics_show_which_part_abstained_and_why():
    d = ar.split_diagnostics(run_split(jul=insufficient("no July rule")))
    feb, jul = d["parts"]
    assert feb["status"] == "answered" and feb["cited_chunk_ids"] == [FEB_CARD.chunk_id]
    assert jul["status"] == "abstained" and jul["abstention_reason"] == "model_insufficient_evidence"
    assert jul["abstention_detail"] == "no July rule" and jul["cited_chunk_ids"] == []
    assert d["final_cited_chunk_ids"] == [FEB_CARD.chunk_id] and (d["parts_answered"], d["parts_abstained"]) == (1, 1)


def test_split_diagnostics_are_none_for_the_single_strategy():
    s = stack(JUL_CARD, FEB_CARD, OTHER)
    assert ar.split_diagnostics(AdaptiveController(s, StubProvider(card_script())).run(FEB_Q).to_dict()) is None
