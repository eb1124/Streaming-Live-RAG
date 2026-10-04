"""LLM providers behind one small interface, so the generation layer does not depend on any vendor.

A provider takes (system prompt, user message, JSON schema) and returns the model's raw JSON text plus
metadata. Everything else (context, validation, citations, abstention) is provider-independent.

  groq  GroqProvider  official `groq` SDK; credentials from the GROQ_API_KEY environment variable
  stub  StubProvider  scripted responses for offline tests (no network)

Add a provider by implementing `complete()` and registering it in `get_provider`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Protocol

from . import config as C


@dataclass
class ProviderResponse:
    text: str  # raw model output (expected: JSON matching the schema)
    provider: str
    model: str
    finish_reason: str | None = None
    usage: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)  # sampling settings actually sent
    system_fingerprint: str | None = None


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str, schema: dict) -> ProviderResponse: ...


class ProviderError(RuntimeError):
    """The provider could not produce a response (network, auth, rate limit, bad request).

    status_code / retry_after (seconds, from a Retry-After header) are set when the provider reports them,
    so callers such as an evaluation harness can tell a rate limit (429) from other failures."""

    def __init__(self, message: str, status_code: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status_code, self.retry_after = status_code, retry_after

    @property
    def rate_limited(self) -> bool:
        return self.status_code == 429


def _retry_after(e: Exception) -> float | None:
    headers = getattr(getattr(e, "response", None), "headers", None) or {}
    try:
        return float(headers.get("retry-after")) if headers.get("retry-after") is not None else None
    except (TypeError, ValueError):
        return None


def _unfinished_document(e: Exception) -> bool:
    """Groq could not finish the structured output: HTTP 400 with code json_validate_failed, or a message saying the
    completion tokens ran out before a valid document. The only provider error that a larger budget can cure."""
    if getattr(e, "status_code", None) != 400:
        return False
    body = getattr(e, "body", None)
    error = body.get("error", body) if isinstance(body, dict) else {}
    code = error.get("code") if isinstance(error, dict) else None
    return code == "json_validate_failed" or "max completion tokens reached" in str(e).lower()


class GroqProvider:
    """Groq chat completions with strict JSON-schema structured output.

    Determinism: temperature 0 and a fixed seed are sent, but Groq documents no determinism guarantee
    for seed; outputs may still vary between runs (hardware/batching). Validation is deterministic
    given an output, and every raw output is stored.

    Completion budget: the model's reasoning and its answer share max_completion_tokens. When the budget runs out
    before the structured output is complete (Groq answers HTTP 400 json_validate_failed, "max completion tokens
    reached before generating a valid document", or returns finish_reason "length"), the same request is sent once
    more with retry_max_completion_tokens. The unfinished output is never used: either a later attempt returns a
    complete document, which is validated like any other, or the call fails with ProviderError.
    """

    name = "groq"

    def __init__(self, model: str = C.GROQ_MODEL, temperature: float = C.TEMPERATURE, seed: int = C.SEED,
                 reasoning_effort: str = C.REASONING_EFFORT, max_completion_tokens: int = C.MAX_COMPLETION_TOKENS,
                 retry_max_completion_tokens: int = C.RETRY_MAX_COMPLETION_TOKENS):
        from groq import Groq  # reads GROQ_API_KEY from the environment; never passed in code

        self.model = model
        self.settings = {"temperature": temperature, "seed": seed, "reasoning_effort": reasoning_effort,
                         "max_completion_tokens": max_completion_tokens}
        self.retry_max_completion_tokens = retry_max_completion_tokens
        self._client = Groq()

    def complete(self, system: str, user: str, schema: dict) -> ProviderResponse:
        import groq

        budgets = [self.settings["max_completion_tokens"]]
        if self.retry_max_completion_tokens > budgets[0]:
            budgets.append(self.retry_max_completion_tokens)
        for attempt, budget in enumerate(budgets, 1):
            settings = {**self.settings, "max_completion_tokens": budget}
            last = attempt == len(budgets)
            try:
                r = self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    response_format={"type": "json_schema",
                                     "json_schema": {"name": "grounded_answer", "strict": True, "schema": schema}},
                    **settings,
                )
            except groq.APIError as e:  # auth, rate limit, bad request, server and connection errors
                if _unfinished_document(e) and not last:
                    continue  # the budget ran out mid-document: once more, with the larger one
                note = f" (completion budget {budget} tokens, attempt {attempt} of {len(budgets)})" if _unfinished_document(e) else ""
                raise ProviderError(f"{type(e).__name__}: {e}{note}", getattr(e, "status_code", None), _retry_after(e)) from e
            choice = r.choices[0]
            if choice.finish_reason == "length":  # cut off at the budget: not a document, whatever it contains
                if not last:
                    continue
                raise ProviderError(f"the completion budget ({budget} tokens) was exhausted before the structured output "
                                    f"was complete (attempt {attempt} of {len(budgets)})")
            usage = r.usage.model_dump() if getattr(r, "usage", None) else {}
            return ProviderResponse(text=choice.message.content or "", provider=self.name, model=r.model,
                                    finish_reason=choice.finish_reason, usage=usage, settings=settings,
                                    system_fingerprint=getattr(r, "system_fingerprint", None))
        raise AssertionError("unreachable: every attempt returns or raises")


class StubProvider:
    """Deterministic scripted provider for tests: `script(system, user)` returns the output dict/str."""

    name = "stub"

    def __init__(self, script: Callable[[str, str], dict | str], model: str = "stub-1"):
        self.script, self.model = script, model
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, schema: dict) -> ProviderResponse:
        self.calls.append((system, user))
        out = self.script(system, user)
        text = out if isinstance(out, str) else json.dumps(out)
        return ProviderResponse(text=text, provider=self.name, model=self.model, finish_reason="stop")


def get_provider(name: str = C.DEFAULT_PROVIDER, **kwargs) -> LLMProvider:
    if name == "groq":
        return GroqProvider(**kwargs)
    raise ValueError(f"unknown provider {name!r} (available: groq; tests use StubProvider directly)")
