"""Phase 2 live runner (offline): phase 1 comparison only where inputs differ, per-case persistence, stop on provider
errors, resume without rewriting done cases, token budget, summary, no request without a key. No network."""

import json

import pytest

from adaptive.controller import AdaptiveController
from adaptive.multi.controller import MultiIntentController
from evaluation.adaptive.run import AllCallsProvider
from evaluation.multi_intent import live
from generation.providers import ProviderError, ProviderResponse, StubProvider
from tests.test_multi_intent_controller import ALIASES, DEADLINE, FILLERS, PENALTY, Q_ONE, Q_TWO, stack, two_part_script


def case(cid, question, multi, strategy=None, intents=()):
    return {"id": cid, "tags": ["test"], "question": question, "multi": multi, "strategy": strategy,
            "intents": list(intents)}


def part(expect, facts=()):
    return {"expect": expect, "gold": [], "facts": list(facts)}


SINGLE = case("t-single", Q_ONE, False, "delegate")
FUSED = case("t-fused", Q_TWO, True, None, [part("answer", ["deadline"]), part("answer", ["suspension"])])
SINGLE2 = case("t-single-2", "What is the deadline to submit card charges promptly?", False, "delegate")
CASES = [SINGLE, FUSED, SINGLE2]


class UsageStub(StubProvider):
    """StubProvider that reports token usage and can fail from a given call on (as the Groq provider raises)."""

    def __init__(self, script, tokens=100, fail_from=None):
        super().__init__(script)
        self.tokens, self.fail_from = tokens, fail_from

    def complete(self, system, user, schema):
        if self.fail_from is not None and len(self.calls) + 1 >= self.fail_from:
            self.calls.append((system, user))
            raise ProviderError("Error code: 400 - schema", status_code=400)
        r = super().complete(system, user, schema)
        return ProviderResponse(r.text, r.provider, r.model, r.finish_reason, usage={"total_tokens": self.tokens})


def setup(inner=None):
    st = stack(DEADLINE, PENALTY, *FILLERS)
    inner = inner or UsageStub(two_part_script())
    provider = AllCallsProvider(inner)
    provider.min_interval = 0.0  # no pacing sleeps in tests
    return MultiIntentController(st, provider, ALIASES), AdaptiveController(st, provider), provider, inner


def quiet(*a):
    pass


# ---------------------------------------------------------------- phase 1 comparison


def test_a_delegated_case_makes_no_duplicate_phase_1_call():
    p2, p1, provider, inner = setup()
    rec = live.run_case(SINGLE, p2, p1, provider)
    assert rec["phase2"]["strategy"] == "delegate" and len(inner.calls) == 1
    assert rec["phase1"]["run"] is False and rec["phase1"]["llm_calls"] == 0 and "shared" in rec["phase1"]["source"]
    assert (rec["phase1"]["status"], rec["phase1"]["text"]) == (rec["phase2"]["status"], rec["phase2"]["text"])
    assert rec["llm_calls"] == 1 and rec["tokens"] == 100 and rec["done"] and rec["checks"] == []


def test_a_non_delegated_case_also_runs_phase_1():
    p2, p1, provider, inner = setup()
    rec = live.run_case(FUSED, p2, p1, provider)
    assert rec["phase2"]["strategy"] == "fused" and rec["phase1"]["run"] is True
    assert rec["phase2"]["llm_calls"] == 1 and rec["phase1"]["llm_calls"] == 1 and len(inner.calls) == 2
    assert PENALTY.chunk_id in inner.calls[0][1] and PENALTY.chunk_id not in inner.calls[1][1]
    assert rec["tokens"] == 200 and rec["checks"] == []
    assert len(rec["phase2"]["claims"]) == 2 and len(rec["phase1"]["claims"]) == 1
    assert rec["phase2"]["citation_intents"] and rec["phase2"]["intents"][1]["status"] == "answered"


def test_checks_report_missing_facts_answered_abstain_intents_and_strategy():
    p2, p1, provider, _ = setup()
    bad = case("t-bad", Q_TWO, True, "per_intent", [part("answer", ["seventy days"]), part("abstain")])
    rec = live.run_case(bad, p2, p1, provider)
    assert rec["checks"] == ["strategy: fused expected per_intent", "intent 1: missing facts ['seventy days']",
                             "intent 2: answered, but the corpus cannot answer it"]


# ---------------------------------------------------------------- persistence, errors, resume


def test_every_case_is_saved_and_a_provider_error_stops_the_run(tmp_path):
    p2, p1, provider, inner = setup(UsageStub(two_part_script(), fail_from=2))
    code = live.run(1, CASES, p2, p1, provider, results=tmp_path, log=quiet)
    assert code == live.EXIT_PROVIDER_ERROR
    data = json.loads((tmp_path / "live-run-1.json").read_text(encoding="utf-8"))
    assert data["complete"] is False and data["cases_total"] == 3 and [r["id"] for r in data["cases"]] == ["t-single", "t-fused"]
    ok, err = data["cases"]
    assert ok["done"] and not ok["provider_errors"]
    assert not err["done"] and "400" in err["provider_errors"][0] and err["phase2"]["abstention_reason"] == "provider_error"
    assert err["phase1"]["run"] is False and "skipped" in err["phase1"]["source"]  # no call into the same failure
    assert len(inner.calls) == 2  # stopped: the third case was not started
    assert sorted(p.name for p in tmp_path.iterdir()) == ["live-run-1.json"]  # no temporary file left


def test_resume_keeps_done_cases_and_redoes_the_errored_one(tmp_path):
    p2, p1, provider, _ = setup(UsageStub(two_part_script(), fail_from=2))
    live.run(1, CASES, p2, p1, provider, results=tmp_path, log=quiet)
    before = json.loads((tmp_path / "live-run-1.json").read_text(encoding="utf-8"))["cases"][0]
    p2, p1, provider, inner = setup()  # e.g. the next day, or another key
    code = live.run(1, CASES, p2, p1, provider, results=tmp_path, log=quiet)
    assert code == live.EXIT_OK
    data = json.loads((tmp_path / "live-run-1.json").read_text(encoding="utf-8"))
    assert data["complete"] is True and [r["id"] for r in data["cases"]] == ["t-single", "t-fused", "t-single-2"]
    assert data["cases"][0] == before  # the done case is never re-run or rewritten
    assert all(r["done"] for r in data["cases"])
    assert len(inner.calls) == 3  # t-fused (phase 2 + phase 1) and t-single-2; nothing for t-single
    assert all(Q_ONE not in u for _, u in inner.calls)


def test_a_complete_run_makes_no_calls(tmp_path):
    p2, p1, provider, _ = setup()
    live.run(1, CASES, p2, p1, provider, results=tmp_path, log=quiet)
    p2, p1, provider, inner = setup()
    assert live.run(1, CASES, p2, p1, provider, results=tmp_path, log=quiet) == live.EXIT_OK and inner.calls == []


def test_token_budget_stops_cleanly_before_the_next_case_and_resumes(tmp_path):
    p2, p1, provider, inner = setup()
    code = live.run(1, CASES, p2, p1, provider, results=tmp_path, token_budget=50, log=quiet)
    assert code == live.EXIT_BUDGET and len(inner.calls) == 1
    data = json.loads((tmp_path / "live-run-1.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in data["cases"]] == ["t-single"] and data["complete"] is False
    p2, p1, provider, inner = setup()
    assert live.run(1, CASES, p2, p1, provider, results=tmp_path, log=quiet) == live.EXIT_OK and len(inner.calls) == 3


def test_separate_run_numbers_use_separate_files(tmp_path):
    for n in (1, 2):
        p2, p1, provider, _ = setup()
        live.run(n, CASES, p2, p1, provider, results=tmp_path, log=quiet)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["live-run-1.json", "live-run-2.json"]


# ---------------------------------------------------------------- summary and CLI


def test_summary_lists_every_case_and_agreement(tmp_path):
    for n in (1, 2):
        p2, p1, provider, _ = setup()
        live.run(n, CASES, p2, p1, provider, results=tmp_path, log=quiet)
    text = live.write_summary(tmp_path)
    assert "## Run 1 (complete): 3/3 done cases pass the checks" in text and "## Run 2 (complete)" in text
    assert all(c["id"] in text for c in CASES) and "same strategy and status in 3/3 cases" in text
    assert (tmp_path / "live-summary.md").read_text(encoding="utf-8") == text


def test_without_a_key_nothing_is_loaded_or_sent(monkeypatch):
    from generation import env, pipeline

    monkeypatch.setattr(env, "load_env", lambda *a, **k: False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(pipeline, "load_stack", lambda *a, **k: pytest.fail("stack loaded without a key"))
    assert live.main(["--run", "1"]) == live.EXIT_NO_KEY


def test_the_runner_writes_only_live_files_in_the_phase_2_results_directory():
    assert live.RESULTS.as_posix().endswith("evaluation/multi_intent/results")
    assert live.run_path(live.RESULTS, 1).name == "live-run-1.json"
    assert not {"offline.json", "offline.md"} & {live.run_path(live.RESULTS, n).name for n in range(1, 5)}
