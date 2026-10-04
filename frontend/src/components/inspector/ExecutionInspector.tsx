// How one answer was produced, stage by stage, from what the backend returned: POST /query (the summary), the
// stored turn (GET /sessions/{id}: resolution, decomposition, per-intent records, model answers) and this page's
// retrieval requests. A stage whose data the backend did not return says so; no stage is drawn that did not happen.

import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";
import type { Phase1Answer } from "../../api/types";
import { claimsOf, count, evidenceShown, inspectionTargets, outcomeOf, words, type Run } from "../../lib/model";
import { useStore } from "../../state/store";
import { RetrievalBlock } from "../RetrievalInspector";
import { EvidenceList } from "../SourceList";
import { Disclosure, Id, MetricRow, Metrics, Note, Pending, Raw, StatusBadge, type Tone } from "../ui";
import { Frame } from "./Frame";

type StepState = "done" | "warn" | "err" | "idle";

function Step({ name, summary, state = "done", children }: { name: string; summary?: ReactNode; state?: StepState; children?: ReactNode }) {
  return (
    <div className={`step ${state}`}>
      <span className="node" aria-hidden />
      <div className="step-head">
        <span className="name">{name}</span>
        {summary ? <span className="summary">{summary}</span> : null}
      </div>
      {children ? <div className="step-body">{children}</div> : null}
    </div>
  );
}

function statusTone(status: string | undefined): Tone {
  return status === "answered" ? "ok" : status ? "warn" : "neutral";
}

function Generation({ part, index, total }: { part: Phase1Answer; index: number; total: number }) {
  return (
    <div className="generation">
      <div className="generation-head">
        <strong>{total > 1 ? `Model answer ${index + 1} of ${total}` : "Model answer"}</strong>
        <StatusBadge tone={statusTone(part.status)}>{part.status}</StatusBadge>
      </div>
      <Metrics>
        {total > 1 ? <MetricRow label="Question">{part.question}</MetricRow> : null}
        <MetricRow label="Strategy">{words(part.decision?.strategy)}</MetricRow>
        <MetricRow label="Model" mono>
          {[part.provider, part.model].filter(Boolean).join(" · ")}
        </MetricRow>
        <MetricRow label="Sources shown">{(part.sources_considered ?? []).length}</MetricRow>
        <MetricRow label="Verified claims">{(part.claims ?? []).length}</MetricRow>
        <MetricRow label="Citations">{(part.citations ?? []).length}</MetricRow>
        {part.abstention_reason ? (
          <MetricRow label="Abstention">
            <Id>{part.abstention_reason}</Id> {part.abstention_detail}
          </MetricRow>
        ) : null}
        {part.verification_problems?.length ? <MetricRow label="Verifier problems">{part.verification_problems.join(" · ")}</MetricRow> : null}
        {part.not_in_sources?.length ? <MetricRow label="Not in the sources">{part.not_in_sources.join(" · ")}</MetricRow> : null}
      </Metrics>
      <Disclosure summary="Decision, prompt version and verifier notes">
        <Metrics>
          <MetricRow label="Decision">{part.decision?.reason}</MetricRow>
          <MetricRow label="Prompt version" mono>
            {part.prompt_version}
          </MetricRow>
          {part.parts?.length ? <MetricRow label="Version parts">{part.parts.map((p) => p.label ?? p.doc_id).join(" · ")}</MetricRow> : null}
          {part.verification_notes?.length ? <MetricRow label="Verifier notes">{part.verification_notes.join(" · ")}</MetricRow> : null}
        </Metrics>
        <Raw label="Decision signals" value={part.decision?.signals ?? {}} />
      </Disclosure>
    </div>
  );
}

export function ExecutionInspector({ run }: { run: Run }) {
  const { openPanel } = useStore();
  const outcome = outcomeOf(run);
  if (!outcome) {
    return (
      <Frame kind="Execution" title={run.question}>
        {run.pending ? <Pending>The query is running. Its execution is shown when the job has answered.</Pending> : <Note tone="err">This turn has no completed response to show.</Note>}
      </Frame>
    );
  }

  const turn = run.turn;
  const answer = turn?.answer ?? null;
  const resolution = turn?.resolution ?? outcome.resolution;
  const unresolved = resolution?.kind === "unresolved";
  const decomposition = answer?.decomposition;
  const multi = decomposition?.multi ?? false;
  const intents = answer?.intents ?? [];
  const targets = inspectionTargets(run);
  const shown = evidenceShown(run);
  const claims = claimsOf(run);
  const parts = answer?.phase1 ?? [];
  const rewritten = resolution && resolution.query && resolution.query !== run.question;
  const missing = run.turnError ? `The stored turn could not be read: ${run.turnError}` : "Reading the stored turn.";

  return (
    <Frame kind="Execution" title={run.question} subtitle={`${multi ? count(intents.length, "intent") : "1 intent"} → retrieval → evidence → generation`}>
      <div className="steps">
        <Step name="Session" summary={resolution ? words(resolution.kind) : undefined} state={unresolved ? "warn" : "done"}>
          {resolution ? (
            <>
              <p className="muted small">{resolution.reason}</p>
              <Metrics>
                {resolution.anchor != null ? <MetricRow label={unresolved ? "Refers to" : "Resolved against"}>question {resolution.anchor + 1} of this session</MetricRow> : null}
                {unresolved ? null : <MetricRow label={rewritten ? "Query, rewritten" : "Query"}>{resolution.query}</MetricRow>}
                {resolution.temporal ? <MetricRow label="Temporal constraint">{resolution.temporal}</MetricRow> : null}
                {resolution.organizations?.length ? <MetricRow label="Organizations">{resolution.organizations.join(", ")}</MetricRow> : null}
              </Metrics>
              {turn ? <Raw label="Resolution signals" value={turn.resolution.signals} /> : null}
            </>
          ) : null}
        </Step>

        {unresolved ? (
          <Step name="Stopped" state="warn" summary="no retrieval, no model call">
            <p className="muted small">The follow-up could not be tied to earlier context safely, so the pipeline abstained before retrieving anything.</p>
          </Step>
        ) : (
          <>
            <Step name="Intent" state={answer ? "done" : "idle"} summary={answer ? (multi ? count(intents.length, "intent") : "1 intent") : undefined}>
              {answer ? (
                <>
                  {decomposition?.reason ? <p className="muted small">{decomposition.reason}</p> : null}
                  {multi ? (
                    <ul className="intent-list">
                      {intents.map((intent) => (
                        <li key={intent.index}>
                          <button className="intent-row" onClick={() => openPanel({ kind: "intent", runId: run.id, index: intent.index }, true)}>
                            <span className="n mono">{intent.index + 1}</span>
                            <span className="body">
                              <span>{intent.text}</span>
                              <span className="where">
                                {[intent.organizations.join(", ") || null, intent.retrieved ? `${intent.retrieved.length} retrieved` : null].filter(Boolean).join(" · ")}
                              </span>
                            </span>
                            {intent.status ? <StatusBadge tone={statusTone(intent.status)}>{intent.status.replace(/_/g, " ")}</StatusBadge> : null}
                            <ChevronRight aria-hidden />
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="muted small">The question was not split: it is answered as one intent.{decomposition?.flags?.length ? ` Flags: ${decomposition.flags.join(", ")}.` : ""}</p>
                  )}
                </>
              ) : (
                <p className="muted small">{missing}</p>
              )}
            </Step>

            <Step name="Retrieval" state={targets.some((t) => run.inspections[t.key]?.result) ? "done" : "idle"} summary={multi ? "whole question, then each intent" : undefined}>
              <p className="faint small">
                The orchestrator does not return its retrieval trace, and the API does not say which mode it ran in. What follows is this page's own
                request to the retrieval service for the same query; retrieval is deterministic, so in the same mode it repeats what the orchestrator
                received.
              </p>
              {targets.map((target) => (
                <RetrievalBlock key={target.key} run={run} target={target} />
              ))}
            </Step>

            <Step name="Evidence" state={shown.length ? "done" : "warn"} summary={`${count(shown.length, "chunk")} shown to the model`}>
              {outcome.strategy ? (
                <p className="muted small">
                  {words(outcome.strategy)}. {answer?.reason}
                </p>
              ) : null}
              {shown.length ? (
                <Disclosure summary="The chunks, in the order of the response">
                  <EvidenceList run={run} entries={shown} push />
                </Disclosure>
              ) : null}
            </Step>

            <Step
              name="Generation"
              state={answer ? (parts.some((p) => p.status === "answered") ? "done" : "warn") : "idle"}
              summary={answer ? `${count(parts.length, "model answer")} · ${count(claims.length, "verified claim")}` : undefined}
            >
              {answer ? (
                parts.length ? (
                  parts.map((part, i) => <Generation key={i} part={part} index={i} total={parts.length} />)
                ) : (
                  <p className="muted small">No model answer was produced for this turn.</p>
                )
              ) : (
                <p className="muted small">{missing}</p>
              )}
            </Step>
          </>
        )}

        <Step name="Answer" state={outcome.answered ? "done" : "warn"} summary={outcome.answered ? count(outcome.citations.length, "citation") : words(outcome.abstentionReason)}>
          <StatusBadge tone={outcome.answered ? "ok" : "warn"}>{outcome.status}</StatusBadge>
        </Step>
      </div>
    </Frame>
  );
}
