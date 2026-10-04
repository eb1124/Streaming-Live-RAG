# Grounded generation layer

```
query -> dense (arctic-m) + BM25 -> RRF -> temporal/version resolution -> cross-encoder rerank
                                                                   (retrieval/, temporal/, unchanged)
      -> evidence gate -> context assembly -> LLM (strict JSON) -> verification -> answer + citations | abstention
                                                                          (generation/)
python -m generation "When is a UConn travel card suspended?"   # needs GROQ_API_KEY; same path as the
                                                                 # integration evaluation (pipeline.retrieve)
python -m evaluation.generation.run [--runs 2]                    # small evidence suite (live)
python -m pytest tests/test_generation.py tests/test_generation_suite.py   # offline
```

| Module | Role |
|---|---|
| `generation/config.py` | All fixed defaults (source limits, gate thresholds, model, sampling) |
| `generation/context.py` | `Evidence` in, numbered `Source`s out; deterministic selection; source headers |
| `generation/prompt.py` | System prompt, user message, strict JSON output schema, `PROMPT_VERSION` |
| `generation/providers.py` | Provider interface; `GroqProvider` (official `groq` SDK); `StubProvider` for tests |
| `generation/validate.py` | Deterministic verification of the model output against the sources |
| `generation/answer.py` | Gate → context → LLM → verify → citations or abstention (`GroundedAnswer`) |
| `generation/pipeline.py` | `retrieve` / `ask`: the one integrated path (hybrid → temporal → rerank → `Evidence` → answer), used by the CLI and `evaluation/integration`; `retrieve_evidence` is the pre-temporal path kept for `evaluation/generation` |

## Context assembly (deterministic)

1. Keep the retriever's order (the reranked order when reranking is on). Nothing is re-sorted.
2. A chunk id appears at most once.
3. Chunks with **identical text in different documents** (same `content_hash`, e.g. unchanged sections of
   the UConn February and July procedures) each get their own citable source label, but the text is
   printed once: the later one reads `(identical text to [Sk]; not repeated)`. Both versions stay
   identifiable and citable (their headers carry their own effective dates); the text costs tokens once.
4. Add sources until `MAX_SOURCES = 6` or `MAX_CONTEXT_TOKENS = 2400` (estimated) would be exceeded. A chunk
   is never cut: one that does not fit is skipped (recorded in `Context.skipped`) and the next is tried.

Each source is printed as one metadata line and its verbatim text:

```
[S2] | organization: University of Connecticut | document: Travel and Entertainment Procedures | type: procedure |
      effective: 2026-07-01 | section: LODGING | pages: 4 | chunk: 2026-07-01-travel-...::004-af625b230e93
Lodging costs may exceed ...
```

Fields that are unknown (no section, no clause, no effective date) are omitted, never filled in.

## Prompt design (`grounded-qa-2`)

* The system prompt fixes the only knowledge source (the numbered sources) and the output contract; the
  question and sources go in the user turn.
* The model answers as **claims**. Each claim lists the source labels that support it and one or more
  **verbatim quotes** from them. The quotes are what make grounding checkable by code.
* "Not enough evidence" is an explicit output state (`insufficient_evidence`), not prose. It is for
  questions the sources do not answer at all.
* **Partial answers** (since `grounded-qa-2`): if the sources answer only part of the question (e.g. one of
  two versions being compared), the model answers that part and lists the unanswered parts in
  `not_in_sources`, as descriptions of missing information, never as facts. Under `grounded-qa-1` the only
  choices were a full answer or abstention; that is why `i-uc-cmp-lodging` abstained with the July rule in context.
* Rules forbid outside knowledge, invented requirements/thresholds/dates/organizations, generalizing one
  organization's rule to another, and merging different documents or versions (each must be cited separately).
* **Versions** (since `grounded-qa-2`): when the context holds several documents of one series, the user turn
  ends with a deterministic "Document versions" list (`context.version_note`), giving each version's effective,
  superseded and current dates and its source labels. A claim may cite several versions only if its quotes appear in
  each of them. A claim that relies on one version must name that version's effective date. If the question
  names no version, each version's rule is reported.
* A value stated in the question (e.g. "a $180,000 contract") may be restated to relate it to the sources.
* Output is constrained by a strict JSON schema (Groq structured outputs, `strict: true`).

## Grounding and verification

The model's output is accepted only if **all** of these hold (`generation/validate.py`):

* it parses as the schema; `answered` has ≥ 1 claim; `insufficient_evidence` has no claims and no `not_in_sources`;
* every claim cites ≥ 1 source, and every cited label exists in the context;
* every claim has ≥ 1 quote with a passage of ≥ 8 characters, and every quote occurs verbatim in the text of
  **one** source that claim cites. The comparison uses normalized text, which removes only characters that
  carry no wording:
  * Unicode compatibility forms (NFKC);
  * hyphen and dash variants (U+2010/U+2011 non-breaking hyphen, en/em dash, minus) mapped to `-`;
  * soft hyphens and zero-width characters dropped;
  * curly quotes;
  * whitespace, including NBSP;
  * case;
  * list-marker glyphs (`•`, `◦`, `▪`, …), and the standalone `o` that Word sub-bullets leave in extracted
    text (45 occurrences in 4 documents, e.g. "business-only o segment").
* a quote may skip text **only at an explicit marker**: a bullet glyph or an elision (`...`, `…`). Its
  passages must then occur in that one source, in order. This proves exactly what separate quotes would
  prove. An unmarked splice (text presented as continuous but not continuous in the source) is rejected;
* every number in the claim (amounts, percentages, days, dates, clause numbers) must occur in one of these
  places. Chunk ids are excluded, because their hash digits would let invented numbers through.
  * The text of a cited source, as digits or as a number word ("seven (7) days").
  * That source's displayed metadata: title, effective/superseded date, clause, section path. So "the July 2026
    procedures" is allowed.
  * The **question**. The user's own value may be restated ("a $180,000 contract falls within …"). Such numbers
    are recorded in `verification_notes`, and a number found in neither the sources nor the question is still
    rejected.
* **versions** (`context.version_groups`: two or more documents of one `series_id` in the context):
  * a claim citing a version must have a quote found in that version;
  * a claim citing several versions is accepted only if each of its quotes from that series occurs in
    **every** cited version, so the rule is common to them. Otherwise it is a merge and is rejected, even if both
    dates are named;
  * a claim relying on a version must name that version's effective date in its text ("July 1, 2026", "July
    2026", "2026-07-01", …; a month name alone does not count). This is waived when:
    * its quotes occur in every version present in the context; or
    * the **question itself** selected exactly that one version. `question_versions` comes from temporal
      resolution: "UConn's February 2026 procedures", "effective July 1, 2026", "currently".
    A compare question (two versions selected) or a neutral question gets no waiver;
  * a version-specific claim that names another version's effective date must also name its own ("Under the July
    1, 2026 procedures …" citing only February is rejected);
* every calendar date written in a claim ("March 1, 2026", "July 2026", "2026-07-01") must occur in the
  question, in the text of a cited source, or in a cited source's effective/superseded date. Month names are not
  numbers, so the number rule alone would accept "March 1, 2026" when the digits 1 and 2026 are in the metadata;
* `not_in_sources` entries contain no numbers except those in the question or in source metadata: they
  describe a gap, never a sourced fact. They are shown after the claims as "The retrieved sources do not
  state: …";
* named entities (`validate.ENTITY_GROUPS`: things of one kind that must not be taken for one another, each with
  its aliases): the group `card` holds the **Travel Card** (Travel Card, University Travel Card, University-Issued
  Travel Card, employee travel card, TCard, T-Card) and the **PCard** (PCard, P-Card, Purchasing Card, Procurement
  Card). A claim that names an entity is rejected when its evidence is about another entity of the same group and
  not about that one: its supporting quotes name the other and none names the claimed one; or its quotes name
  neither, and the sources they were found in (text and displayed metadata) name the other and not the claimed
  one. Evidence that names both supports the entity it names where the claim is supported; evidence that names no
  entity of the group ("the card") is not judged by this rule. Aliases are matched on normalized text.

If any check fails, **nothing the model wrote is shown**: the result is the fixed abstention, with the raw
output and the list of problems kept for inspection.

## Evidence-sufficiency rule and abstention

Abstain with the fixed text *"The retrieved documents do not contain enough evidence to answer this
question."* and a reason code when:

| Code | When | LLM called? |
|---|---|---|
| `no_evidence` | no source survives context assembly | no |
| `low_relevance` | cross-encoder scores are present and the best is < 0.0 (the cross-encoder's own decision boundary, sigmoid 0.5; fixed, not tuned) | no |
| `model_insufficient_evidence` | the model returns `insufficient_evidence` | yes |
| `ungrounded_output` | the output fails verification (above) | yes |
| `provider_error` | the provider call fails (auth, rate limit, network) | attempted |

## Citations

Every answer sentence (claim) ends with `[n]` markers. Each citation records the context label, the exact
`chunk_id` and `doc_id` (the chunk text can always be retrieved), organization, document title, effective
date when known, page or page range, section path and clause when available. Missing fields are left out.

```
Travel Card charges must be submitted within 30 days ...[1]
[1] University of Connecticut — Travel and Entertainment Procedures (effective 2026-07-01),
    EXPENSE REPORTING > University Travel Card Penalties, p. 5 · chunk 2026-07-01-...::005-73722dbc7d99
```

## Model and provider

* **Evaluation model:** Groq `openai/gpt-oss-20b` (131K context, 65K max output), called through the official
  `groq` SDK with strict JSON-schema output. Credentials come only from `GROQ_API_KEY`.
* **Settings:** temperature 0, seed 1234, `reasoning_effort` "medium", `max_completion_tokens` 4096.
* **Completion budget:** the model's reasoning and its answer share `max_completion_tokens`. When the budget runs out
  before the structured output is complete (Groq: HTTP 400 `json_validate_failed`, "max completion tokens reached
  before generating a valid document", or `finish_reason` "length"), the same request is sent once more with
  `RETRY_MAX_COMPLETION_TOKENS` = 16384. An unfinished output is never used: a complete document from the second
  attempt is verified like any other; otherwise the call fails with `ProviderError` and the answer is an abstention
  with reason `provider_error`. No other provider error is retried here. `ProviderResponse.settings` records the
  budget of the attempt that returned.
* **Provider-neutral:** `LLMProvider.complete(system, user, schema) -> ProviderResponse`. Replacing Groq means
  adding one adapter class; context assembly, prompt, verification, citations and abstention do not change.

## Determinism

Everything except the LLM call is deterministic (context selection, prompt text, verification, citation
numbering). Groq does not document a determinism guarantee for `seed`; with temperature 0 outputs may still
vary between runs (batching and hardware numerics). Every raw output is stored, and
`python -m evaluation.generation.run --runs 2` measures run-to-run agreement (outcome, citations, text).

## Known limitations

* No temporal logic: version questions rely on the effective dates shown in source headers and on the prompt;
  retrieval may still rank the other version first.
* Verification checks quotes and numbers, not whether the claim *means* what the quote says. A claim could
  quote real text yet misstate a condition; the evidence suite checks key facts, not full semantics.
* Numbers written in a different form than the source (e.g. an ISO date for "July 1, 2026") are rejected
  (conservative: abstains rather than risk a wrong number).
* A question number restated in a claim is accepted without checking what the claim does with it: "the limit
  is $500" (from the question) with a quote saying "$300" would pass the number check. The quote must still be
  real source text, and every such case is listed in `verification_notes`.
* Version attribution checks that the right effective date is *named*, not that the sentence attributes the
  rule correctly in meaning.
* `not_in_sources` text is model-written and unverified beyond the number rule; it is displayed as a gap in
  the retrieved sources, not as a statement about the documents.
* A claim that fails verification makes the whole answer abstain (a partial answer is possible only when the
  model itself reports the unanswered part in `not_in_sources`).
* The evidence suite is 13 hand-written behavioral cases, not an answer-quality benchmark.
