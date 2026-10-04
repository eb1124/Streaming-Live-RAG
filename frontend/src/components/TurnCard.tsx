// One turn of a conversation: the question, the grounded answer, its sources. The machinery behind it is one click
// away (the inspector), not on the page.

import { Activity, Workflow } from "lucide-react";
import { count, evidenceShown, ms, outcomeOf, runState, type Run } from "../lib/model";
import { useStore } from "../state/store";
import { AnswerPanel } from "./AnswerPanel";
import { EvidenceList, SourceList } from "./SourceList";
import { Disclosure, StatusBadge } from "./ui";

export function TurnCard({ run }: { run: Run }) {
  const { panel, openPanel } = useStore();
  const state = runState(run);
  const outcome = outcomeOf(run);
  const resolution = outcome?.resolution ?? null;
  const shown = outcome ? evidenceShown(run) : [];
  const loading = Object.values(run.inspections).some((i) => i.pending);
  const selected = panel?.runId === run.id;
  const rewritten = resolution && resolution.kind === "follow_up" && resolution.query && resolution.query !== run.question;

  return (
    <article className={`turn ${selected ? "selected" : ""}`} aria-label={`Question: ${run.question}`}>
      <header className="turn-q">
        <span className="turn-n mono">{outcome?.turnIndex != null ? String(outcome.turnIndex + 1).padStart(2, "0") : "··"}</span>
        <h2>{run.question}</h2>
      </header>

      <div className="turn-a">
        {rewritten ? (
          <p className="resolved">
            <span className="k">Follow-up{resolution.anchor != null ? ` to question ${resolution.anchor + 1}` : ""}, read as</span>
            {resolution.query}
          </p>
        ) : null}

        <div className="answer-head">
          <span className="eyebrow">{state === "abstained" ? "No grounded answer" : "Grounded answer"}</span>
          {state === "answered" ? <StatusBadge tone="ok">Answered</StatusBadge> : null}
          {state === "abstained" ? <StatusBadge tone="warn">Abstained</StatusBadge> : null}
          {state === "failed" || state === "error" ? <StatusBadge tone="err">Failed</StatusBadge> : null}
          {run.stored ? (
            <span className="faint small" title="This turn was read from the stored session (GET /sessions/{id}); this page did not ask it.">
              from the stored session
            </span>
          ) : null}
        </div>

        <AnswerPanel run={run} />

        {outcome && outcome.citations.length ? (
          <section className="turn-section">
            <h3 className="eyebrow">Sources</h3>
            <SourceList run={run} citations={outcome.citations} />
          </section>
        ) : null}

        {outcome && shown.length ? (
          <Disclosure summary={`Evidence shown to the model · ${count(shown.length, "chunk")}`}>
            <EvidenceList run={run} entries={shown} />
          </Disclosure>
        ) : null}

        {outcome || (!run.pending && run.exchange) ? (
          <footer className="turn-foot">
            {outcome ? (
              <button className={`btn small ${panel?.kind === "execution" && selected ? "on" : ""}`} onClick={() => openPanel({ kind: "execution", runId: run.id })}>
                <Workflow aria-hidden /> Inspect execution
              </button>
            ) : null}
            <button className={`btn small ghost ${panel?.kind === "telemetry" && selected ? "on" : ""}`} onClick={() => openPanel({ kind: "telemetry", runId: run.id })}>
              <Activity aria-hidden /> Telemetry
            </button>
            <span className="meta">
              {loading ? (
                <span role="status" className="loading">
                  <span className="spinner" aria-hidden /> loading evidence text
                </span>
              ) : null}
              {run.exchange ? <span title="Time from sending POST /query to its response, measured in this browser">{ms(run.exchange.ms)}</span> : null}
            </span>
          </footer>
        ) : null}
      </div>
    </article>
  );
}
