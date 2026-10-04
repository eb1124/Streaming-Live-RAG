"""The Groq provider's completion budget (offline): a structured output that runs out of completion tokens is asked
again once with a larger budget; an unfinished output is never used; every other provider error is what it was.
A fake client stands in for the Groq SDK's (no network, no key).
"""

import json
from types import SimpleNamespace

import groq
import httpx
import pytest

from generation import config as C
from generation.answer import GroundedAnswerer
from generation.prompt import ANSWER_SCHEMA
from generation.providers import GroqProvider, ProviderError
from tests.test_generation import CARD, answered, ev

REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
QUOTE = "charges must be submitted within 30 days"
GOOD = answered(("Travel Card charges must be submitted within 30 days of the trip end date", ["S1"], [QUOTE]))
GOOD["not_in_sources"] = []
# what the model had written when the budget ran out: claims, but not the fields that close the document
PARTIAL = '{"status": "answered", "claims": [{"text": "Travel Card charges must be submitted within 30 days", "sources": ["S1"], "quotes": ["' + QUOTE + '"]}]'


def api_error(cls, status, message, code=None, **extra):
    body = {"error": {"message": message, "type": "invalid_request_error", "code": code, **extra}}
    return cls(f"Error code: {status} - {body}", response=httpx.Response(status, request=REQUEST), body=body)


def truncated():
    """The failure observed live: the budget ran out before the document's required fields were written."""
    return api_error(groq.BadRequestError, 400, "max completion tokens reached before generating a valid document",
                     "json_validate_failed", failed_generation=PARTIAL)


def completion(content, finish="stop"):
    return SimpleNamespace(model=C.GROQ_MODEL, system_fingerprint="fp", usage=None,
                           choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))])


class FakeClient:
    """chat.completions.create(**kwargs): records the call, then raises or returns the next scripted outcome."""

    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def budgets(self):
        return [c["max_completion_tokens"] for c in self.calls]


def provider(monkeypatch, *outcomes, **kwargs):
    monkeypatch.setenv("GROQ_API_KEY", "not-a-key")  # the SDK client is constructed, then replaced: no request is made
    p = GroqProvider(**kwargs)
    p._client = FakeClient(*outcomes)
    return p


def test_the_configured_budgets():
    assert C.MAX_COMPLETION_TOKENS == 4096  # the first attempt is the recorded evaluation setting, unchanged
    assert C.MAX_COMPLETION_TOKENS < C.RETRY_MAX_COMPLETION_TOKENS <= 65536  # within the model's limit on Groq


def test_a_complete_output_takes_one_call_at_the_configured_budget(monkeypatch):
    p = provider(monkeypatch, completion(json.dumps(GOOD)))
    r = p.complete("system", "user", ANSWER_SCHEMA)
    assert json.loads(r.text) == GOOD and p._client.budgets == [C.MAX_COMPLETION_TOKENS]
    assert r.settings == p.settings and r.finish_reason == "stop"


@pytest.mark.parametrize("first", [
    truncated(),
    api_error(groq.BadRequestError, 400, "Failed to generate JSON. Please adjust your prompt.", "json_validate_failed",
              failed_generation=PARTIAL),
    completion(PARTIAL, finish="length"),
])
def test_an_output_cut_off_at_the_budget_is_asked_again_once_with_the_larger_budget(monkeypatch, first):
    p = provider(monkeypatch, first, completion(json.dumps(GOOD)))
    r = p.complete("system", "user", ANSWER_SCHEMA)
    client = p._client
    assert client.budgets == [C.MAX_COMPLETION_TOKENS, C.RETRY_MAX_COMPLETION_TOKENS]
    same = [{k: v for k, v in c.items() if k != "max_completion_tokens"} for c in client.calls]
    assert same[0] == same[1]  # the same request: messages, schema, strict mode, temperature, seed, reasoning effort
    assert same[0]["response_format"]["json_schema"] == {"name": "grounded_answer", "strict": True, "schema": ANSWER_SCHEMA}
    assert json.loads(r.text) == GOOD and r.settings["max_completion_tokens"] == C.RETRY_MAX_COMPLETION_TOKENS
    assert p.settings["max_completion_tokens"] == C.MAX_COMPLETION_TOKENS  # the next question starts at the usual budget


def test_the_answer_after_a_retry_is_verified_like_any_other(monkeypatch):
    p = provider(monkeypatch, truncated(), completion(json.dumps(GOOD)))
    a = GroundedAnswerer(p).answer("When are Travel Card charges due?", ev(CARD))
    assert (a.status, a.abstention_reason) == ("answered", None) and [c.chunk_id for c in a.citations] == [CARD.chunk_id]
    bad = answered(("Charges are due within 45 days", ["S1"], [QUOTE]))
    bad["not_in_sources"] = []
    p = provider(monkeypatch, truncated(), completion(json.dumps(bad)))
    a = GroundedAnswerer(p).answer("When are Travel Card charges due?", ev(CARD))
    assert (a.status, a.abstention_reason) == ("abstained", "ungrounded_output")  # the verifier is as strict as before


def test_an_output_still_unfinished_at_the_larger_budget_is_a_provider_error_and_is_never_used(monkeypatch):
    p = provider(monkeypatch, truncated(), truncated())
    with pytest.raises(ProviderError) as info:
        p.complete("system", "user", ANSWER_SCHEMA)
    assert info.value.status_code == 400 and not info.value.rate_limited and len(p._client.calls) == 2
    assert f"completion budget {C.RETRY_MAX_COMPLETION_TOKENS} tokens, attempt 2 of 2" in str(info.value)

    p = provider(monkeypatch, truncated(), truncated())
    a = GroundedAnswerer(p).answer("When are Travel Card charges due?", ev(CARD))
    assert (a.status, a.abstention_reason) == ("abstained", "provider_error")  # a provider failure, not an answer
    assert a.text == C.ABSTENTION_TEXT and a.claims == [] and a.citations == [] and a.raw_output is None
    assert "max completion tokens reached" in a.abstention_detail


@pytest.mark.parametrize("content", [PARTIAL, json.dumps(GOOD)])
def test_an_output_marked_as_cut_off_is_not_accepted_even_when_it_parses(monkeypatch, content):
    p = provider(monkeypatch, completion(content, finish="length"), completion(content, finish="length"))
    with pytest.raises(ProviderError, match="exhausted before the structured output was complete"):
        p.complete("system", "user", ANSWER_SCHEMA)
    p = provider(monkeypatch, completion(content, finish="length"), completion(content, finish="length"))
    a = GroundedAnswerer(p).answer("When are Travel Card charges due?", ev(CARD))
    assert (a.status, a.abstention_reason, a.citations) == ("abstained", "provider_error", [])


@pytest.mark.parametrize("error, status", [
    (api_error(groq.RateLimitError, 429, "Rate limit reached", "rate_limit_exceeded"), 429),
    (api_error(groq.AuthenticationError, 401, "Invalid API Key", "invalid_api_key"), 401),
    (api_error(groq.BadRequestError, 400, "response_format: invalid schema", "invalid_request_error"), 400),
    (api_error(groq.InternalServerError, 500, "internal error"), 500),
])
def test_other_provider_errors_are_not_retried(monkeypatch, error, status):
    p = provider(monkeypatch, error, completion(json.dumps(GOOD)))
    with pytest.raises(ProviderError) as info:
        p.complete("system", "user", ANSWER_SCHEMA)
    assert info.value.status_code == status and len(p._client.calls) == 1 and "attempt" not in str(info.value)
    assert info.value.rate_limited is (status == 429)


def test_a_failure_of_the_retry_itself_is_reported_as_it_is(monkeypatch):
    limited = api_error(groq.RateLimitError, 429, "Rate limit reached", "rate_limit_exceeded")
    p = provider(monkeypatch, truncated(), limited)
    with pytest.raises(ProviderError) as info:
        p.complete("system", "user", ANSWER_SCHEMA)
    assert info.value.rate_limited and len(p._client.calls) == 2


def test_no_retry_when_no_larger_budget_is_configured(monkeypatch):
    p = provider(monkeypatch, truncated(), completion(json.dumps(GOOD)), retry_max_completion_tokens=C.MAX_COMPLETION_TOKENS)
    with pytest.raises(ProviderError):
        p.complete("system", "user", ANSWER_SCHEMA)
    assert p._client.budgets == [C.MAX_COMPLETION_TOKENS]


def test_a_genuine_insufficient_evidence_answer_is_still_an_abstention_of_the_model_not_a_provider_error(monkeypatch):
    none = {"status": "insufficient_evidence", "claims": [], "not_in_sources": [], "abstention_reason": "not stated"}
    p = provider(monkeypatch, completion(json.dumps(none)))
    a = GroundedAnswerer(p).answer("What is the mileage rate?", ev(CARD))
    assert (a.status, a.abstention_reason) == ("abstained", "model_insufficient_evidence") and len(p._client.calls) == 1
    p = provider(monkeypatch, truncated(), completion(json.dumps(none)))  # also when it took the larger budget to say so
    a = GroundedAnswerer(p).answer("What is the mileage rate?", ev(CARD))
    assert a.abstention_reason == "model_insufficient_evidence" and len(p._client.calls) == 2
