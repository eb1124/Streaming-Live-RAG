// The answer of one turn, exactly as the backend returned it: answered text with its citation markers, an
// abstention shown as an abstention, a failed job shown as a failure. Nothing is substituted.

import { useEffect, useState } from "react";
import { abstentionTitle, answerByIntent, answerByOrganization, outcomeOf, segments, words, type Run } from "../lib/model";
import { useStore } from "../state/store";
import { IntentAnswers, OrganizationAnswers } from "./IntentAnswers";
import { Disclosure, ErrorState, Id, Pending } from "./ui";

function Elapsed({ since }: { since: string }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, []);
  return <span className="mono">{Math.max(0, Math.round((now - new Date(since).getTime()) / 1000))} s</span>;
}

export function AnswerPanel({ run }: { run: Run }) {
  const { panel, openPanel } = useStore();
  const r = run.response;

  if (run.pending) {
    return (
      <Pending>
        Waiting for <span className="mono">POST /query</span>: the job runs retrieval and generation before it answers.{" "}
        {run.startedAt ? <Elapsed since={run.startedAt} /> : null}
      </Pending>
    );
  }
  if (r?.status === "failed") {
    return (
      <ErrorState title="The job failed">
        The pipeline raised <span className="mono">{r.error?.type ?? "an error"}</span> and the API answered HTTP 502. Nothing was stored for this
        turn.
        <pre className="raw">{r.error?.message ?? ""}</pre>
        <span className="faint">
          job <span className="mono">{r.job_id}</span>
        </span>
      </ErrorState>
    );
  }
  const outcome = outcomeOf(run);
  if (!outcome) {
    return (
      <ErrorState title="The query did not complete">
        {run.error ?? "No response."}
        {run.exchange?.status ? (
          <>
            {" "}
            <span className="mono">HTTP {run.exchange.status}</span>
          </>
        ) : null}
      </ErrorState>
    );
  }

  const byIntent = answerByIntent(run);
  if (!outcome.answered) {
    const detail = run.turn?.answer?.abstention_detail;
    const block = (
      <div className="abstained">
        <h3>{abstentionTitle(outcome.abstentionReason)}</h3>
        <p>
          {words(outcome.abstentionReason)}. The system returned no answer rather than an ungrounded one.
          {outcome.resolution?.kind === "unresolved" ? " No retrieval and no model call were made for this turn." : ""}
        </p>
        {detail ? <p>{detail}</p> : null}
        {outcome.text ? <p className="said">“{outcome.text}”</p> : null}
        <p className="faint small">
          abstention_reason <Id>{outcome.abstentionReason ?? "none"}</Id>
        </p>
      </div>
    );
    return byIntent ? (
      <>
        {block}
        <IntentAnswers run={run} answer={byIntent} />
      </>
    ) : (
      block
    );
  }

  const active = panel?.kind === "source" && panel.runId === run.id ? panel.citation : null;
  const text = (
    <div className="answer-text">
      {segments(outcome.text ?? "", outcome.citations).map((s, i) => {
        if (s.kind === "text") return <span key={i}>{s.text}</span>;
        const citation = outcome.citations.find((c) => c.number === s.number)!;
        return (
          <button
            key={i}
            className={`marker ${active === s.number ? "on" : ""}`}
            onClick={() => openPanel({ kind: "source", runId: run.id, chunkId: citation.chunk_id, citation: s.number })}
            aria-label={`Open the source of citation ${s.number}`}
            title={citation.document ?? citation.doc_id}
          >
            {s.number}
          </button>
        );
      })}
    </div>
  );
  // A question the backend split into intents is read intent by intent; the text it returned stays one click away.
  const byOrganization = byIntent ? null : answerByOrganization(run);
  if (!byIntent && !byOrganization) return text;
  return (
    <>
      {byIntent ? <IntentAnswers run={run} answer={byIntent} /> : <OrganizationAnswers run={run} answer={byOrganization!} />}
      <Disclosure summary="Answer text as returned">{text}</Disclosure>
    </>
  );
}
