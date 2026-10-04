# Adaptive retrieval controller (AdaptiveRAG phase 1)

```
query -> generation.pipeline.retrieve            (frozen: hybrid -> temporal -> rerank -> top 10)
      -> decide                                  (adaptive/controller.py; no model, no similarity measure)
      -> GroundedAnswerer.answer                 (frozen: gate -> context -> LLM -> verification), once or once per version
      -> AdaptiveAnswer

python -m adaptive "How long do UConn travelers have to submit Travel Card charges?"   # needs GROQ_API_KEY
python -m evaluation.adaptive.run --runs 2          # live: the 27 integration cases through the controller
python -m evaluation.adaptive.run --summarize       # rewrite summary.md from saved runs; no LLM calls
python -m pytest tests/test_adaptive_controller.py tests/test_adaptive_eval_persistence.py   # offline
```

| Module | Role |
|---|---|
| `adaptive/controller.py` | `AdaptiveController(stack, provider)`: `run(query)` = `answer(retrieve(stack, query))`; the decision, the per-version split and the combination; `AdaptiveAnswer`, `Decision` |
| `adaptive/__main__.py` | CLI: prints the answer, the strategy, the reason and the decision signals (`--json` for the full record) |
| `evaluation/adaptive/run.py` | Live evaluation over the 27 integration cases, compared with the frozen baseline; writes `evaluation/adaptive/results/` |

The controller is orchestration only. It calls the frozen functions as they are:
`generation.pipeline.retrieve`, `generation.pipeline.answer_retrieval` (= `GroundedAnswerer(provider).answer(query,
evidence, question_versions)`), `generation.context.assemble` and `version_groups`. It changes no retrieval, no
temporal resolution, no prompt, no verifier rule and no model text.

## Strategies

* **`single`**: exactly `generation.pipeline.answer_retrieval`, which is what `ask()` and `python -m generation` do.
* **`split_by_version`**: one independent grounded answer per version of one document series, each from evidence
  that contains only that version, combined into sections labeled from chunk metadata.

## Decision (deterministic)

1. **Eligible** when all three hold:
   * the question is version-neutral (temporal intent `neutral`, `temporal/intent.py`);
   * the context the answerer would assemble (`assemble(r.evidence)`, the same call) holds two or more versions of
     one series (`version_groups`);
   * those versions' chunks in the context differ: their `content_hash` sets are not equal.

   Exactly one series must qualify; with two or more, the question is not eligible ("not supported"). A chunk hash
   covers a whole section, so a difference only shows that *something* in the retrieved sections differs, not
   that the rule the question needs differs.
2. **The single path always runs first.** If the question is eligible and the verifier rejected that answer **only**
   for version attribution (every problem contains one of `"merges versions"`, `"must name that version's
   effective date"`, `"but cites only"`: `VERSION_ATTRIBUTION_PROBLEMS`, pinned by the tests against the real
   verifier), the controller discards the rejected answer and answers per version.
3. Anything else is final: an accepted answer, other verification problems, an abstention, a provider error.

So a question costs one LLM call, or 1 + (number of versions) when it splits. Questions where the versions carry
identical evidence, or where the single answer is accepted, never pay for extra calls.

## The per-version split

For each version (ordered by effective date):

* evidence = every retrieved chunk **except** the other versions of the split series (`evidence_for_version`; ranks
  unchanged), so shared sources from other documents stay available;
* `GroundedAnswerer.answer(query, evidence, question_versions={series: [doc_id]})`: the part is verified as if the
  question had named that version, so claims need not repeat its date, but they must still come from it;
* the section label comes from metadata only: `"<organization> <title>, version effective <effective_date>"`.

The combined answer joins the parts' verified claims, one section per version. Citations are renumbered across
parts by first use (one number per chunk). A part that abstains becomes the section text
`"<label>: the retrieved sources for this version do not answer the question."` and a gap entry. If every part
abstains, the answer abstains (the parts' common reason, or `split_all_parts_abstained`). The rejected single
answer is kept in `AdaptiveAnswer.single_attempt`; each part's full answer is in `parts`.

## Output (`AdaptiveAnswer`)

The `GroundedAnswer` fields with the same meaning, plus:

* `decision`: `strategy`, `reason`, and `signals`: temporal intent and trigger, selected versions, temporal flags,
  the versions in the context with their labels and content-hash prefixes, the series with differing evidence,
  the best rerank score, and the single attempt's status, abstention reason and version problems;
* `single_attempt` and `parts` (split only).

## Evaluation (`evaluation/adaptive/`)

The 27 integration cases (`evaluation/integration/cases.py`, unchanged) with the integration checks
(`evaluation.integration.run.check`), provider pacing and bounded 429 retries, compared per case with the frozen
baseline (`evaluation/integration/results/`, tag `rag-baseline-2026-09-28`, read only). Each `run-<n>.json` is
rewritten atomically after every case (`"complete": false` until the last), so an interrupted run keeps its
finished cases; `summary.md` and `agreement.json` are written when all runs finish, or by `--summarize`. For split
cases, `split_diagnostics` records each part's status, citations and whether they survived into the final answer.

Committed result (2 runs, groq `openai/gpt-oss-20b`, prompt `grounded-qa-2`): run 1 20/27, run 2 22/27 automatic
checks (baseline 23/27, 21/27), 0 provider errors. Retrieval, temporal filtering, reranking, context and status
agreed 27/27 between runs. `i-uc-neutral-card` passed in both runs through `split_by_version` (both parts answered,
all citations kept). Every failing case took the `single` path with the same evidence and prompt size as the
baseline; the differences are model output variance (for example quotes wrapped in literal `"`, which the
verbatim quote check rejects).

## Frozen boundary

Not modified by phase 1: ingestion, chunking, embeddings, dense retrieval, BM25, RRF, reranking, temporal
resolution, context assembly, the prompt, the verifier, `ask()`, `python -m generation`, the 27 integration cases
and `evaluation/integration/results/`. `tests/test_adaptive_controller.py` checks that `ask()` and the verifier
behave as before.

## Limitations

* Only one versioned series can be split per question.
* Eligibility compares whole-section hashes, not the specific rule the question needs; the verifier's rejection
  is what confirms that the versions' relevant evidence differs.
* A split costs one extra LLM call per version.
