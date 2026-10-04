# Multi-intent decomposition and evidence fusion (AdaptiveRAG phase 2)

```
query -> decompose                                   (adaptive/multi/decompose.py; rules, no model)
   one intent ------------------------------------------------------> phase 1: AdaptiveController.answer(retrieve(query))
   several -> retrieve(query) = r0, retrieve(sub-query) per intent   (frozen generation.pipeline.retrieve)
           -> organization boundary, relevance gate, coverage gate   (adaptive/multi/fusion.py)
           -> delegate | fused | per_intent | no_supported_intent    (adaptive/multi/controller.py)
           -> phase 1 controller -> frozen answerer and verifier
           -> MultiIntentAnswer (answer, citations, intents, claim -> intent map)

python -m adaptive.multi "What is the UConn Travel Card submission deadline, and what happens if charges are late?"
python -m evaluation.multi_intent.run          # offline: real retrieval, stub model, no tokens
python -m pytest tests/test_multi_intent_decompose.py tests/test_multi_intent_controller.py   # offline
```

| Module | Role |
|---|---|
| `adaptive/multi/decompose.py` | `decompose(question, organizations, aliases)`: intents, sub-queries, organizations named / carried / outside the corpus |
| `adaptive/multi/fusion.py` | `IntentRetrieval` (admissible, best chunk), `covered`, `fuse`, `associated`, `fused_retrieval` |
| `adaptive/multi/controller.py` | `MultiIntentController(stack, provider)`: `run(query)` -> `MultiIntentAnswer`; strategy choice and assembly |
| `adaptive/multi/__main__.py` | CLI: answer, strategy, reason, and each intent's sub-query, admissibility, coverage and citations |
| `config/organization_aliases.toml` | Short organization names used in questions -> the `organization` value in chunk metadata (7 entries) |
| `evaluation/multi_intent/` | 16 phase 2 cases and the offline runner (`results/offline.json`, `results/offline.md`) |

Phase 2 sits above phase 1 and changes neither phase 1 nor the RAG core. It calls `retrieve`, `assemble` and
`AdaptiveController.answer` as they are; every model call goes through phase 1 and the unchanged answerer and
verifier.

## Decomposition (deterministic)

A question has several intents when it asks two or three separate questions:

* two question sentences: `What is X? When does Y apply?`
* coordinated interrogative clauses: `..., and what ...`, `... and when ...`, `..., and is ...` (an auxiliary needs
  the comma), `...; how ...`, and a serial list `..., how ..., and when ...` (bare-comma splits only when the list
  ends with `, and <wh-word>`);
* one question about a list of organizations' subjects: `What are UConn's travel advance requirements, Rutgers
  University's Travel Card rules, and McGill University's non-travel advance policy?`. Only a `What / Which is / are
  ...` question, and only when every list item starts with an organization (a corpus organization, an alias, or an
  institution-style name), names no second one, has a subject after it, and the organizations all differ. Each item
  becomes the intent `What are <item>?`, in the user's words. A list part that names no organization belongs to the
  item before it (`Rutgers' approval and settlement rules`), so `UConn's and Rutgers' Travel Card rules`, `the
  differences between UConn's ... and Rutgers' ...` and a list within one organization stay one question.

* institution-led clauses: `At UConn, what is X, and at Rutgers, what is Y?`, `For UConn, what is X; for Rutgers,
  what is Y?`, and the serial form `At A, ..., at B, ..., and at C, ...?`. A clause is cut where `and` / `;` (or, in
  a serial list ending in `, and at ...`, a comma) is followed by a scope `at | for | in | under | within ..., ` and
  then a question, and only when that scope names an institution (a corpus organization, an alias, or an
  institution-style name). Such a clause keeps its own scope instead of the first clause's; a first scope that names
  no institution (`Under the procedures effective July 1, 2026, `) still applies to it;
* one question asked of a list of institutions: `What are the Travel Card rules at UConn and Rutgers?`, `At UConn,
  Rutgers and McGill, what are the travel advance rules?`. The list must follow `at | for | in`, hold nothing but
  two or more different institutions (every institution the question names), joined by commas, `and` or `or`, and
  end the question or its leading scope. Each intent is the question with the list replaced by one institution. A
  question that compares them (`between`, `difference`, `compare`, `same`, `both`, `either`, `than`, `versus`) is
  not split.

Short names of institutions come from `config/organization_aliases.toml` (`UConn`, `Rutgers`, `McGill`, ...).

Every intent records its `topic` when it is a `What is / are ...` or `what about ...` question: the noun phrase asked
for, without the organization that leads it (`travel advance requirements`); empty for other question forms.

It is **not** split (the question goes to phase 1 unchanged) when:

* the temporal intent is `compare` (two or more dates): versions are the temporal layer's and phase 1's concern;
* there is no question sentence (`Compare the February and July rules.`); a non-question sentence after a question
  belongs to it (`... ? Explain why.`);
* the word after `and` does not start a question (`tips and gratuities`, `review and approval`, `$50,000 or more`);
* a clause is shorter than 3 words (`..., and when?`);
* a later clause refers back to the previous one (`it`, `its`, `they`, `them`, `their`, `this`, `that`, `these`,
  `those`, `such`): `..., and how often is that?` is not an independent need;
* there are more than `MAX_INTENTS = 3` clauses (flag `too_many_intents`).

**Sub-queries** are built only from the user's words: the context sentences before the first question, the
sentence's leading scope (`Under UConn's July 1, 2026 procedures, `, `At Penn, `: the longest comma-terminated prefix
without a question word), and the clause. A clause that names no organization, while the other clauses name exactly
one, gets that organization's full name appended in parentheses (`what happens if charges aren't submitted on time?
(University of Connecticut)`). With two or more different organizations elsewhere, nothing is carried (flag
`ambiguous_organization:intent_k`). An organization is recognized by its full metadata name (case-insensitive) or a
short name from `config/organization_aliases.toml` (exact case, optional possessive). Only organizations are carried,
not other terms.

**Organization boundary.** In a question with several intents, an intent whose own words name an institution-style
name (`<Name> University|College|Institute`, `University|College|Institute of <Name>`) that is not a corpus
organization or alias (and does not contain or sit inside one), and that name no corpus organization, records it in
`Intent.outside_corpus`. Single-intent questions are never marked (phase 1 decides them, unchanged).

## Per-intent retrieval and the gates

Each intent's sub-query goes through the frozen `retrieve()`: hybrid retrieval, its **own temporal resolution**, the
rerank pool, reranking, top 10. The whole question is retrieved as well (`r0`, the retrieval phase 1 would use).

**Entity alignment** (`adaptive/multi/entity.py`, applied to every retrieval the controller makes: the whole
question's and each intent's). A question names an entity when it writes a term as a name: a capitalized phrase
(`Travel Card`) or a cased compound (`PCard`); organizations and institutions are not entities, and a question written
without capitals names none. When the retrieved evidence holds a chunk that names another thing of the same head as a
name (`PCard`, `Purchasing Card` for a Travel Card question) and does not name the asked one, the retrieval is
*contested*: those chunks are removed, so they are never shown, quoted or cited. If fewer than two of the chunks that
would then be shown name the asked entity, the retrieval is taken again from the whole reranked pool (20) and the
first two chunks that name it (`employee travel card (TCard)`) are placed first; everything else keeps the reranker's
order. An uncontested retrieval is returned untouched. Retrieval, reranking, the prompt and the verifier are unchanged.

An intent whose own words name organizations keeps, of what its retrieval returned, only those organizations' chunks
(`fusion.retrieve_intent`; order, ranks and scores unchanged, chunks without an organization kept). Another
organization's chunk is therefore never that intent's evidence. An organization carried from another clause does not
filter, and an intent that names none keeps its whole retrieval. If nothing of its organization was retrieved, the
intent is unsupported.

* **Admissible**: the intent is not outside the corpus, and its best cross-encoder score is
  `>= MIN_RERANK_LOGIT` (0.0, the answerer's own low-relevance threshold). An intent outside the corpus is never
  admissible, however high the reranker scores another organization's chunk for it.
* **Covered**: the intent's best chunk, or a chunk with the same `content_hash`, is already in `assemble(r0.evidence)`,
  the context phase 1 would show the model.

## Strategies

| Strategy | When | Model calls |
|---|---|---|
| `delegate` | one intent; or several, none outside the corpus, and every admissible intent covered (or none admissible) | phase 1 exactly: same retrieval, same model input |
| `fused` | an admissible intent is not covered, or an intent is outside the corpus; and every admissible intent selects the same versions as the whole question | 1 (more only if phase 1 splits by version) |
| `per_intent` | as `fused`, but the admissible intents select different versions | 1 per admissible intent |
| `no_supported_intent` | several intents, at least one outside the corpus, none admissible | 0: abstains (`unsupported_organization`) |

A question with an intent outside the corpus is never delegated as a whole, because the whole question's context
may hold chunks that were retrieved only because of that clause.

**Versions.** Selections are compared only for version series that occur in the admissible intents' evidence, so a
date in a clause about another organization matters only when that series' chunks were retrieved. The verifier's
version rules depend on which versions the question selected (`question_versions`), so intents that select
different versions cannot be verified in one call; that is why `per_intent` exists.

**Fusion** (`fused`): the admissible intents' evidence is interleaved by rank (intent 1 rank 1, intent 2 rank 1,
intent 1 rank 2, ...), each chunk once with every intent that retrieved it, and ranks renumbered in that order. The
unchanged `assemble()` (`MAX_SOURCES = 6`, `MAX_CONTEXT_TOKENS = 2400`) then gives every intent a fair share. The fused
evidence goes to `AdaptiveController.answer` as a `Retrieval` of the **original question** with the whole question's
temporal resolution: the model sees the user's question, not the sub-queries, and phase 1 may still split by version.

**Per intent** (`per_intent`): `AdaptiveController.answer(intent retrieval)` for each admissible intent (the model
sees that intent's sub-query), combined into one section per intent, labeled `Part k (<clause>)`. Citations are
renumbered by first use across parts. An intent without admissible evidence gets no call and the section
`Part k (<clause>): no relevant evidence was retrieved for this part.` (or `... names an organization outside the
corpus (<name>).`). If no part is answered, the answer abstains.

## Unsupported intents

An intent without admissible evidence contributes no evidence, gets no model call and is never marked answered.
In the fused path the model still sees the whole question; the frozen prompt makes it list unanswerable parts in
`not_in_sources`, and the verifier rejects any claim whose quote is not in a shown source. Phase 2 adds no text
of its own there.

## Traceability

* `evidence_intents`: every chunk shown to the model -> the intents that retrieved it, with rank and score.
* `claims[i]["intents"]`: the intents served by the chunks the claim cites. A chunk serves the intents that scored it
  `>= MIN_RERANK_LOGIT`, or, if none did, the intent that scored it highest (`fusion.associated`). Intents that are
  not admissible never claim evidence.
* `citation_intents`: citation number -> the intents of the claims that cite it.
* `intents[k]`: clause, sub-query, organizations, carried, outside_corpus, temporal intent, selected versions, best
  score, admissible, best chunk, covered by the question's context, the retrieved chunk ids, `status`
  (`answered` | `not_answered` | `unsupported`) and its citation numbers.
* `phase1`: the phase 1 answer(s) the final answer was built from (with their own decisions).

## Evaluation

`python -m evaluation.multi_intent.run` runs every question through phase 1 and phase 2 on the live corpus with the
real retrieval stack and a stub model that answers `insufficient_evidence` (no network, no tokens). It records the
decomposition, strategy, per-intent retrieval and gates, whether each intent's gold evidence reaches the model's
context under phase 1 and under phase 2, and whether phase 2's model input is byte-identical to phase 1's.

Offline result (`evaluation/multi_intent/results/offline.md`):

* 16/16 phase 2 cases pass the offline checks (decomposition as declared, determined strategy, identical input
  when delegating, every answerable intent's gold evidence shown to the model).
* Evidence recovered that phase 1's context lacked: `mi-st-ro`, `mi-mi-ru-dated`, `mi-uc-mixed-temporal` (the July
  multi-bedroom rule, removed by the whole question's February filter), 2 of 3 intents of `mi-three`; `mi-ut-ru`
  is answered, where phase 1 stops at the relevance gate without a model call.
* Coverage gate kept phase 1 unchanged: `mi-uc-card`, `mi-penn`, `mi-mi-air`, `mi-or-emergency`, `mi-uc-jul-scope`.
* Organization boundary: in `mi-ro-harvard` the Harvard intent is outside the corpus (reranker score 3.5 on other
  organizations' bid rules) and the model's context holds only University of Rochester chunks.
* The 27 integration questions: 2 decompose (`i-uc-jul-submit`, `i-uc-personal-leg`), all 27 delegate, and all 27 give
  model input identical to phase 1.

`python -m evaluation.multi_intent.live --run N` runs the same 16 cases with the configured LLM (Groq): phase 2, plus
phase 1 as a separate comparison only when phase 2 did not delegate (a delegated answer is phase 1's own). It saves
`results/live-run-N.json` after every case, resumes after a provider error, and `--summarize` rewrites
`results/live-summary.md` without LLM calls. Its checks are automatic, not a verdict on correctness.

Live run 1 (`evaluation/multi_intent/results/live-summary.md`): 16/16 cases done, 14/16 pass the automatic checks, 28
LLM calls, ~87k tokens, 0 provider errors. The two failed checks were reviewed; neither is a phase 2 defect:

* `mi-or-emergency` (delegate): both gold chunks were in the context and the coverage gate was right; the model
  quoted the emergency clause with an unmarked elision, the verifier rejected it, and the whole answer abstained.
  Generation/model behaviour.
* `mi-uc-mixed-temporal` (per_intent, February and July versions selected correctly): the answer is grounded and
  verified; "personal credit card" is in the claim's verbatim quote, but the check searches only the claim text.
  Evaluation-check limitation.

## Frozen boundary

Not modified by phase 2: ingestion, chunking, embeddings, dense retrieval, BM25, RRF, reranking, temporal
resolution, context assembly, the prompt, the verifier, phase 1 (`adaptive/controller.py`, `adaptive/__main__.py`,
`evaluation/adaptive/`), the 27 integration cases and all earlier results. `tests/test_multi_intent_controller.py`
checks byte-identical model input and answers against phase 1 for single-intent and covered questions, and phase 1's
public interface.

## Limitations

* Only institution-style names are recognized as organizations outside the corpus; a bare name (`Harvard`) is not.
  An intent naming both a corpus organization and an outside one stays supported.
* References are resolved only for organizations: clauses that refer back are not split, and elliptical clauses get
  only the organization carried over.
* A date in one clause can lead to `per_intent` when that series' chunks are retrieved (`mi-mi-ru-dated`,
  `mi-three`): correct, but 2 to 3 model calls instead of 1.
* The coverage gate looks at each intent's best chunk only.
