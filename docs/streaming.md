# AdaptiveRAG phase 6: bounded iterative retrieval

One retrieval of the question can miss evidence the question needs: the clearest case is a question naming two
organizations where one organization's chunks fill all six context slots. Phase 6 adds a controlled loop above the
frozen core: retrieve, check whether the evidence the model would be shown covers the request, and if it does not,
retrieve again with a deterministic refinement of the question, at most 3 rounds in all. The model is called once,
after the loop, through the unchanged phase 1 answer path. Retrieval, reranking, temporal resolution, the prompt and
the verifier are not changed. It is not an agent: there is no model in the loop, no tool choice, and a fixed budget.

```
question -> retrieve(question)                         round 1 = generation.pipeline.retrieve (frozen)
         -> coverage (rules)                           sufficient | structurally_unresolved -> stop
         -> insufficient: next refinement              none new -> stop;  round budget reached -> stop
                -> retrieve(refinement) -> merge -> coverage ...
         -> AdaptiveController.answer(retrieval)       phase 1: gate, context, model (once), verifier
```

`python -m adaptive.streaming 'question' [--max-rounds N] [--offline] [--json]` prints the answer, each round's
query, strategy, evidence counts and coverage decision, and the stopping reason.

## Files

| file | role |
|---|---|
| `adaptive/streaming/controller.py` | `IterativeRetriever` (the loop, a drop-in for `retrieve`), `StreamingController` |
| `adaptive/streaming/coverage.py` | `assess`: the coverage rules |
| `adaptive/streaming/refine.py` | `refinements`, `keywords`, `strip_organizations` |
| `adaptive/streaming/state.py` | `Coverage`, `Round`, `RetrievalTrace`, `StreamingRetrieval`, `StreamingAnswer` |
| `adaptive/streaming/__main__.py` | the CLI |
| `adaptive/multi/controller.py`, `adaptive/multi/fusion.py`, `adaptive/session/controller.py` | changed: an optional `retriever` (default: the frozen `retrieve`) |
| `evaluation/streaming/` | offline baseline-vs-loop evaluation; `cases.py`: two phase 6 cases |
| `tests/test_streaming.py` | 21 offline tests |

## Coverage: a heuristic, stated exactly

Coverage is judged on the context the answerer would assemble from the evidence (`assemble`: at most 6 sources), with
the existing cross-encoder's score **for the question itself** (a source is relevant at `MIN_RERANK_LOGIT` = 0.0 or
above, the answerer's own low-relevance threshold). In this order:

1. **structurally unresolved**: the question names only institutions outside the corpus
   (`decompose.outside_corpus`), or nothing is relevant and the requested version does not exist
   (`requested_version_unavailable`). No retrieval can change that; the loop stops.
2. **sufficient, unmeasured**: the evidence has no cross-encoder scores (a stack without a reranker). Coverage cannot
   be measured; the frozen rule applies (one round).
3. **insufficient**: nothing was retrieved; or no source is relevant; or a corpus organization the question names
   (`decompose.mentions`: full names and aliases) has no relevant source in the context.
4. **sufficient**: otherwise.

That is all it checks. It does not know whether the relevant sources contain the answer, whether every part of a
question is covered when one organization is named, or anything about meaning beyond the cross-encoder score. It is
a rule over existing signals, not a confidence estimate.

## Refinement

Deterministic, from the question's own words, tried in this order:

* **organization_focus**, one per organization without a relevant source: the question with the other named
  organizations removed and the missing one's full name appended: `... what is the travel advance rule (University
  of Connecticut)`.
* **keyword_focus**: the question without function words (question words, auxiliaries, articles, pronouns,
  prepositions); names, numbers, amounts, dates and "current"-type words are kept.

A candidate is issued only if it is new (not an earlier query, ignoring case and spacing) and keeps the question's
temporal intent (`parse_query`: same kind and dates), so a refinement cannot select other versions. Candidates not
issued are recorded in the trace with the reason. With no candidate left the loop stops.

## Evidence accumulation

After one round the frozen `Retrieval` is returned unchanged, so a question that is covered at once gets exactly the
frozen path's evidence and model input. After several rounds:

* **identity** is the chunk id; a chunk retrieved again is kept once. Chunks of a version the question's own temporal
  resolution excluded are never retained (a safeguard: refinements keep the temporal intent).
* **scores**: every retained chunk carries its cross-encoder score for the question (round 1's scores are kept; a new
  chunk is scored once with `reranker.rerank(question, ...)`). Refined queries only widen the candidates; they never
  score. The answerer's relevance gate therefore judges the same thing as without the loop.
* **order**: by that score, ties in the order first retrieved: a stronger chunk is never displaced by a weaker one,
  except by promotion.
* **promotion**: a named organization with no relevant source in the context gets its best relevant retained chunk
  moved into the last context slot(s) (never the first); the chunk it displaces stays in the retained evidence.
* **retained**: the top 10 (`RETRIEVE_K`) of that order, ranks renumbered; the `Retrieval` keeps the question, round
  1's temporal resolution and candidate lists.

## Stopping

`sufficient`, `structurally_unresolved`, `no_new_refinement`, or `max_rounds` (default 3, the first round
included; `max_rounds=1` is the frozen path). Every round issues a new query and the budget is fixed, so the loop
always ends. If the evidence is still insufficient at the end, the answer path decides as it always did: the
relevance gate abstains without a model call, or the model and the verifier abstain.

## Trace

`StreamingRetrieval.trace` (`RetrievalTrace`): the question, `max_rounds`, per round the iteration, query, strategy
(and target organization), chunk ids retrieved, new, excluded, retained and shown, promotions, the coverage decision
with its reason and signals, and the decision (`refine: ...` / `stop: ...`); the skipped refinements and the stop
reason. No timings, so identical inputs give identical traces.

## Tracing (phase 10A)

Since phase 10A each round is an OpenTelemetry span, when the loop runs in the retrieval service
(`RETRIEVAL_MODE=iterative`, or `mode=iterative` of the MCP `document_search`):

```
retrieval.execute                retrieval.mode=iterative, retrieval.rounds, retrieval.stop_reason
├── retrieval.round              retrieval.round.number=1, strategy=initial, decision=refine
├── retrieval.round              number=2, strategy=organization_focus, decision=stop, retrieval.stop_reason
└── ...
```

A round span has the round's number, strategy, the counts of chunks retrieved, new, excluded, retained, shown and
promoted, the coverage decision (`retrieval.coverage.decision`, `retrieval.coverage.achieved`), `refine` or `stop`,
and on the last round the stop reason. Its start and end are when the round ran: the timing the trace does not have.

`RetrievalTrace` is not replaced and not changed. It stays the loop's deterministic execution state, returned with
the retrieval; the span only reads counts and decisions from the round's entry. The round's query, chunk ids,
coverage reason and target organization are in the trace only, never in a span.

The spans are made by `services/retrieval/rounds.py` (`TracedIterativeRetriever`, a subclass that observes where a
round ends); nothing in `adaptive/streaming` changed, and the traced loop returns the same evidence and trace
(`tests/test_telemetry.py`). The loop run directly (`python -m adaptive.streaming`, `StreamingController`) is not
traced. Without an exporter configured the spans are no-ops. See [telemetry.md](telemetry.md).

## Sessions, jobs, stored sessions

* **Phase 3**: `SessionController(stack, provider, retriever=IterativeRetriever())`. Follow-up resolution is
  unchanged; the loop runs on the query phase 2 receives (the rewritten query for a follow-up), for the current turn
  only. `IterativeRetriever` holds no state between calls, so nothing is carried from earlier turns.
* **Phase 2**: with a `retriever`, every retrieval phase 2 makes (the whole question and each intent's sub-query) goes
  through the loop; decomposition, the coverage gate, fusion and the strategies are unchanged.
* **Phase 4**: the worker calls the session controller as before; the job lifecycle and contracts are unchanged
  (tested). The jobs CLI keeps the frozen retrieval.
* **Phase 5**: the trace is execution state: it is not part of the `Session`, so nothing about the loop is stored.
* **Phase 10A**: one tracing span per round in the retrieval service (see Tracing above).

## Evaluation

`python -m evaluation.streaming.run` (`evaluation/streaming/results/offline.md`), real retrieval stack, stub model:

* 27 integration questions: 24 are covered at once (1 round, the frozen evidence); 3 take 3 rounds and stay
  insufficient (`i-st-mileage`, `i-ro-tips`, `i-uc-nyc-hotel`, all questions the corpus cannot answer: the extra
  rounds find nothing relevant and the model input stays the same); `i-harvard-bids` is structurally unresolved.
  The model input equals the baseline's for 27/27, including the 20 whose baseline answer passed every check in every
  committed live run: their answers are unchanged.
* 16 phase 2 cases and 17 phase 3 turns: model input, strategies and resolutions unchanged. For 4 phase 2 questions
  naming two organizations, the whole-question retrieval takes 3 rounds without closing the gap (phase 2's own
  per-intent retrieval covers them): 2 extra retrievals each, no effect.
* 2 phase 6 cases, from the phase 2 manual review (not a blind test): for "At UConn, ... suspension rule, and at
  Rutgers, ... travel advance rule?" (not decomposed) round 1 shows six Rutgers sources; round 2 promotes UConn's
  suspension rule into the context, and both gold units are shown. It is the only question whose model input changes;
  its answer needs a live run.

No answer is evaluated offline, and no live run was made for this phase.

## Early retrieval on an incremental transcript

`adaptive.session.gate.predicts_retrieval(partial)` is the retrieval-intent prediction for a transcript that is still
arriving: true once the words so far hold two content words (words outside the gate's function-word, opener, request
and small-talk vocabularies). It never replaces the decision on the complete utterance (`gate.decide`): an utterance
that trails off still waits, an acknowledgement is still suppressed.

`evaluation/early/run.py` measures it: each utterance is fed word by word on a deterministic clock (a word every
400 ms). The first prefix that predicts retrieval is resolved against the session and retrieved with the
controller's retriever: the trigger, with its timestamp. The complete utterance then goes through
`SessionController.ask`, unchanged. Eligible utterances are the questions of the existing suites for which the
controller required retrieval; the no-retrieval utterances are in `evaluation/early/cases.py`.
Results: `evaluation/early/results/offline.md` (`python -m evaluation.early.run`; real stack, no model).

The early retrieval is a pre-fetch. The answer is still grounded in the retrieval of the complete utterance, the
services take complete utterances, and nothing reuses the pre-fetched evidence yet; the evaluation reports how much
of the final context the pre-fetch already held.

## Limitations

* Coverage is per named organization, not per part of the question: a question with two parts about one
  organization is covered as soon as one relevant source exists.
* A question that names no corpus organization can only be judged by relevance; an institution outside the corpus
  named next to a corpus organization (`Harvard University` and `Penn`) does not affect coverage.
* A gap is closed only by a chunk that is relevant **to the whole question**; a multi-part question can dilute that
  score below the threshold, so the loop sometimes retrieves the right organization's chunk and still cannot use it
  (the 4 phase 2 cases above).
* Extra rounds cost retrieval time (a hybrid retrieval and a cross-encoder pass each) even when they change nothing.
* Refinements are two fixed rules; they do not reformulate meaning.
* The phase 1 answer path may itself call the model more than once (a version split), and phase 2's `per_intent`
  answers each part separately, as before: the loop never adds a call, but "once" means once per answer phase 1 makes.
