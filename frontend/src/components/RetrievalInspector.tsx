// One retrieval inspection: a real POST /retrieve for a query of this turn, and what it returned. The rounds are
// the RetrievalTrace of that response (application execution state, not telemetry); a round is listed only if the
// trace contains it.

import { Play } from "lucide-react";
import { useState } from "react";
import type { RetrievalMode, Round } from "../api/types";
import { count, ms, seconds, type InspectionTarget, type Run } from "../lib/model";
import { useStore } from "../state/store";
import { Disclosure, ErrorState, Id, MetricRow, Metrics, Pending, Raw, StatusBadge } from "./ui";

export function RetrievalRound({ round }: { round: Round }) {
  const sufficient = round.coverage.decision === "sufficient";
  return (
    <div className="round">
      <div className="round-head">
        <strong>Round {round.iteration}</strong>
        <span className="muted">
          {round.strategy.replace(/_/g, " ")}
          {round.target ? ` · ${round.target}` : ""}
        </span>
        <StatusBadge tone={sufficient ? "ok" : "warn"}>coverage {round.coverage.decision.replace(/_/g, " ")}</StatusBadge>
      </div>
      <dl className="facts">
        <div>
          <dt>retrieved</dt>
          <dd>{round.retrieved.length}</dd>
        </div>
        <div>
          <dt>new</dt>
          <dd>{round.new.length}</dd>
        </div>
        <div>
          <dt>retained</dt>
          <dd>{round.retained.length}</dd>
        </div>
        <div>
          <dt>in context</dt>
          <dd>{round.context.length}</dd>
        </div>
        {round.promoted.length ? (
          <div>
            <dt>promoted</dt>
            <dd>{round.promoted.length}</dd>
          </div>
        ) : null}
        {round.excluded_versions.length ? (
          <div>
            <dt>excluded versions</dt>
            <dd>{round.excluded_versions.length}</dd>
          </div>
        ) : null}
      </dl>
      <p className="round-decision">
        <span className="k">decision</span> {round.decision || "—"}
      </p>
      <p className="muted small">{round.coverage.reason}</p>
      <Disclosure summary="Query and chunk ids of this round">
        <Metrics>
          <MetricRow label="Query">{round.query}</MetricRow>
        </Metrics>
        <Raw label="Chunk ids and coverage signals" value={{ retrieved: round.retrieved, new: round.new, retained: round.retained, context: round.context, promoted: round.promoted, excluded_versions: round.excluded_versions, coverage_signals: round.coverage.signals }} />
      </Disclosure>
    </div>
  );
}

export function RetrievalBlock({ run, target }: { run: Run; target: InspectionTarget }) {
  const { inspect, health, settings } = useStore();
  const inspection = run.inspections[target.key];
  const [mode, setMode] = useState<RetrievalMode>(inspection?.request.mode ?? settings.mode);
  const [maxRounds, setMaxRounds] = useState(inspection?.request.max_rounds ?? settings.maxRounds);
  const result = inspection?.result;
  const trace = result?.trace ?? null;

  return (
    <div className="retrieval">
      <div className="retrieval-head">
        <strong>{target.label}</strong>
        {result ? (
          <span className="muted small">
            {result.mode} · {count(result.evidence.length, "chunk")}
            {trace ? ` · ${count(trace.rounds.length, "round")}` : ""}
          </span>
        ) : null}
      </div>
      <p className="muted small query">{target.query}</p>

      {!inspection ? (
        <p className="faint small">Not loaded. Sends this query to the retrieval service (POST /retrieve) and shows its rounds and evidence.</p>
      ) : inspection.pending ? (
        <Pending>
          Waiting for <span className="mono">POST /retrieve</span>
        </Pending>
      ) : inspection.error || !result ? (
        <ErrorState title="The retrieval request failed">{inspection.error ?? "No response."}</ErrorState>
      ) : (
        <>
          {trace ? (
            <>
              <div className="rounds">
                {trace.rounds.map((round) => (
                  <RetrievalRound key={round.iteration} round={round} />
                ))}
              </div>
              <p className="stop">
                <span className="k">stop reason</span> {trace.stop_reason.replace(/_/g, " ")}
                <span className="faint"> · budget {count(trace.max_rounds, "round")}</span>
              </p>
            </>
          ) : (
            <p className="muted small">Single mode has no rounds: one hybrid retrieval, temporal version resolution and rerank.</p>
          )}
          {trace && trace.skipped.length ? (
            <Disclosure summary={`${count(trace.skipped.length, "refinement")} considered and not issued`}>
              <Metrics>
                {trace.skipped.map((s, i) => (
                  <MetricRow key={i} label={`${s.strategy.replace(/_/g, " ")}: ${s.why}`}>
                    {s.query}
                  </MetricRow>
                ))}
              </Metrics>
            </Disclosure>
          ) : null}
          <Disclosure summary="Request, server timings and temporal resolution">
            <Metrics>
              <MetricRow label="Request" mono>
                POST /retrieve · mode={inspection.request.mode} · max_rounds={inspection.request.max_rounds} · k={inspection.request.k}
              </MetricRow>
              <MetricRow label="Request id" mono>
                {result.request_id}
              </MetricRow>
              <MetricRow label="Round trip" mono>
                {ms(inspection.exchange?.ms)}
              </MetricRow>
              {Object.entries(result.seconds).map(([stage, value]) => (
                <MetricRow key={stage} label={`Server time: ${stage.replace(/_/g, " ")}`} mono>
                  {seconds(value)}
                </MetricRow>
              ))}
              <MetricRow label="Temporal intent">{result.resolution.kind.replace(/_/g, " ")}</MetricRow>
              <MetricRow label="Temporal trigger">{result.resolution.trigger}</MetricRow>
              <MetricRow label="Selected versions">
                {Object.keys(result.resolution.selected).length
                  ? Object.entries(result.resolution.selected).map(([series, docs]) => (
                      <div key={series}>
                        <Id>{series}</Id> → {docs.map((d) => <Id key={d}>{d}</Id>)}
                      </div>
                    ))
                  : null}
              </MetricRow>
              <MetricRow label="Candidates seen">{result.resolution.candidates}</MetricRow>
              <MetricRow label="Dropped (other versions)">{result.resolution.dropped.length}</MetricRow>
            </Metrics>
          </Disclosure>
        </>
      )}

      <div className="retrieval-run">
        <select className="select" aria-label="Retrieval mode" value={mode} onChange={(e) => setMode(e.target.value as RetrievalMode)}>
          <option value="iterative">iterative</option>
          <option value="single">single</option>
        </select>
        <select className="select" aria-label="Round budget" value={maxRounds} disabled={mode === "single"} onChange={(e) => setMaxRounds(Number(e.target.value))}>
          {[1, 2, 3, 4, 5].map((n) => (
            <option key={n} value={n}>
              max {n}
            </option>
          ))}
        </select>
        <button
          className="btn small"
          disabled={inspection?.pending || health.retrieval.state === "down"}
          onClick={() => void inspect(run.id, target.key, target.label, target.query, { mode, maxRounds })}
        >
          <Play aria-hidden /> {inspection ? "Run again" : "Run retrieval"}
        </button>
      </div>
    </div>
  );
}
