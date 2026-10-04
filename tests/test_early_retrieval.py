"""Early retrieval (offline): the retrieval-intent prediction on a partial transcript, and the word-by-word harness of
evaluation/early/run.py, over the keyword stand-ins of tests/test_session.py. No network, no models.
"""

import pytest

from adaptive.session.controller import SessionController
from adaptive.session.gate import EARLY_MIN_CONTENT_WORDS, RETRIEVE, content_words, decide, predicts_retrieval
from adaptive.session.state import FOLLOW_UP, PRESENTATION, SUPPRESS, WAIT, Session
from evaluation.early.cases import ACKNOWLEDGEMENTS, LAYOUT_REQUESTS, UNFINISHED
from evaluation.early.run import WORD_MS, simulate, summarize
from generation.providers import StubProvider
from tests.test_multi_intent_controller import stack
from tests.test_session import ALIASES, CHUNKS, top_source

Q = "When must Rutgers travel advance requests be submitted?"


def prefixes(utterance):
    words = utterance.split()
    return [" ".join(words[:k]) for k in range(1, len(words) + 1)]


# ---------------------------------------------------------------- the prediction (pure)


@pytest.mark.parametrize("partial, expected", [
    ("What", False), ("What are the", False), ("What are the UConn", False),  # an organization alone is not yet a subject
    ("What are the UConn travel", True), ("What are the UConn travel advance requirements", True),
    ("When must Rutgers travel", True), ("uconn travel advance", True),
    ("Does that apply", False), ("What about the", False), ("What about the submission deadline", True),
    ("Actually, the trip was", False), ("Actually, the trip was international", True),
])
def test_prediction_needs_content_not_length(partial, expected):
    assert EARLY_MIN_CONTENT_WORDS == 2 and predicts_retrieval(partial) is expected


@pytest.mark.parametrize("utterance", ACKNOWLEDGEMENTS + LAYOUT_REQUESTS + UNFINISHED)
def test_no_prefix_of_an_utterance_that_needs_no_retrieval_predicts_retrieval(utterance):
    assert [p for p in prefixes(utterance) if predicts_retrieval(p)] == []


def test_content_words_exclude_the_gates_vocabularies():
    assert content_words("Thanks, that's all for now") == []
    assert content_words("Can you please give me that in 3 bullet points") == []
    assert content_words("What are the UConn travel advance requirements?") == ["uconn", "travel", "advance", "requirements"]


def test_prediction_does_not_replace_the_decision_on_the_complete_utterance():
    assert predicts_retrieval("What are the UConn travel advance...") and decide("What are the UConn travel advance...").kind == WAIT
    assert predicts_retrieval(Q) and decide(Q).kind == RETRIEVE


# ---------------------------------------------------------------- the harness


class Harness:
    def __init__(self):
        self.provider = StubProvider(top_source)
        self.controller = SessionController(stack(*CHUNKS), self.provider, ALIASES)
        self.retrievals = []
        base = self.controller.multi.retrieve
        self.controller.multi.retrieve = lambda st, q, k=10: self.retrievals.append(q) or base(st, q, k)
        self.session = Session()
        self.ticks = iter(range(0, 10 ** 6))

    def run(self, utterance):
        return simulate(self.controller, self.session, utterance, clock=lambda: next(self.ticks) * 0.05)


def test_a_question_triggers_retrieval_before_its_last_word_with_timestamps():
    h = Harness()
    rec = h.run(Q)
    assert rec["words"] == 8 and rec["completion_ms"] == 8 * WORD_MS
    t = rec["trigger"]
    assert (t["word"], t["ms"], t["partial"], t["query"]) == (4, 4 * WORD_MS, "When must Rutgers travel", "When must Rutgers travel")
    assert rec["early"] and not rec["false_trigger"] and rec["lead_ms"] == 4 * WORD_MS
    assert t["retrieval_ms"] == 50.0 and rec["early_and_done"]  # the injected clock: one tick of 50 ms
    assert (rec["decision"], rec["retrieval_required"]) == (RETRIEVE, True)
    assert h.retrievals == ["When must Rutgers travel", Q]  # the pre-fetch, then the complete utterance: unchanged path
    assert len(h.provider.calls) == 1 and h.session.turns[0].question == Q  # one turn, answered from the complete question
    assert rec["final_context"] and 0 < rec["context_overlap"] <= 1


def test_an_early_retrieval_that_outlasts_the_utterance_is_early_but_not_done():
    h = Harness()
    h.ticks = iter(range(0, 10 ** 6, 100))  # each retrieval takes 5 s on the injected clock
    rec = h.run(Q)
    assert rec["early"] and rec["trigger"]["retrieval_ms"] == 5000.0 and not rec["early_and_done"]


def test_a_follow_up_is_pre_fetched_with_the_session_context():
    h = Harness()
    h.run(Q)
    rec = h.run("What documentation is required after the trip?")
    assert rec["resolution"] == FOLLOW_UP and rec["early"]
    assert rec["trigger"]["query"].startswith("(Earlier in this conversation: When must Rutgers travel advance requests be submitted). ")
    assert rec["trigger"]["resolution"] == FOLLOW_UP


@pytest.mark.parametrize("utterance, decision", [
    ("Thanks, that's all.", SUPPRESS), ("Give me that in two bullet points.", PRESENTATION), ("What about the...", WAIT),
])
def test_no_retrieval_starts_for_an_utterance_that_needs_none(utterance, decision):
    h = Harness()
    h.run(Q)
    before = list(h.retrievals)
    rec = h.run(utterance)
    assert rec["trigger"] is None and not rec["early"] and not rec["false_trigger"]
    assert (rec["decision"], rec["retrieval_required"]) == (decision, False) and h.retrievals == before


def test_a_trigger_for_an_utterance_that_ends_unfinished_is_counted_as_a_false_trigger():
    h = Harness()
    rec = h.run("When must Rutgers travel advance requests be...")
    assert rec["decision"] == WAIT and rec["trigger"] is not None and rec["false_trigger"] and not rec["early"]


def test_summary_counts_only_what_was_recorded():
    h = Harness()
    records = [{"suite": "a", **h.run(Q)}, {"suite": "a", **h.run("What about tips?")},
               {"suite": "b", **h.run("Thanks, that's all.")}, {"suite": "b", **h.run("When must Rutgers travel advance requests be...")}]
    s = summarize(records)
    assert (s["utterances"], s["eligible"], s["early"], s["early_rate"]) == (4, 2, 1, 0.5)  # a short follow-up is a miss
    assert (s["no_retrieval_utterances"], s["false_triggers"], s["false_trigger_rate"]) == (2, 1, 0.5)
    assert s["by_suite"] == {"a": {"utterances": 2, "eligible": 2, "early": 1, "false_triggers": 0},
                             "b": {"utterances": 2, "eligible": 0, "early": 0, "false_triggers": 1}}
    assert s["mean_lead_ms"] == 4 * WORD_MS
