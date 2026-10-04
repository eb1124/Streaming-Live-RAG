// Telemetry as this page can honestly show it. The services export their spans to the configured OpenTelemetry
// exporter; no API returns them. What the page knows first-hand:
//   * the trace context it sent with each request (the browser starts the trace),
//   * the `traceresponse` each service answered with: the ids of its own server span, present only while that
//     service records spans,
//   * what the responses say happened inside (rounds from the RetrievalTrace, statuses, counts).
// Rows marked "exported" are spans the service creates for that request (docs/telemetry.md); their ids are in the
// exporter, not in any response, and are shown as not exposed.

import type { ReactNode } from "react";
import type { Exchange } from "../api/client";
import { ms, type Inspection, type Run } from "../lib/model";
import { Id } from "./ui";

export interface SpanRow {
  depth: number;
  op: string;
  service: string;
  attrs?: string;
  spanId?: string | null; // undefined: exported, id not exposed
  duration?: number | null;
  status?: "ok" | "error" | null;
  /** true: the service exports this span; "off": the service answered without a traceresponse, so it recorded none. */
  exported?: boolean | "off";
}

function Row({ row }: { row: SpanRow }) {
  const id = row.spanId ? `span ${row.spanId}` : row.exported === "off" ? "not recorded" : row.exported ? "span id not exposed" : "";
  return (
    <div className="span" style={{ paddingLeft: 12 + row.depth * 16 }}>
      <div className="span-line">
        <span className={`span-dot ${row.status ?? ""}`} aria-hidden />
        <span className="op">{row.op}</span>
        {row.duration != null ? (
          <span className="dur" title="Round trip measured in this browser">
            {ms(row.duration)}
          </span>
        ) : null}
      </div>
      <div className="span-meta">
        {[row.service, row.attrs, id].filter(Boolean).join(" · ")}
      </div>
    </div>
  );
}

export function TraceTree({ title, traceId, note, rows, badge }: { title: string; traceId: string | null; note?: ReactNode; rows: SpanRow[]; badge?: ReactNode }) {
  return (
    <div className="trace">
      <div className="trace-head">
        <strong>{title}</strong>
        {badge}
      </div>
      <div className="trace-id">trace id {traceId ? <Id>{traceId}</Id> : <span className="faint">not exposed</span>}</div>
      <div className="trace-rows">
        {rows.map((row, i) => (
          <Row key={i} row={row} />
        ))}
      </div>
      {note ? <p className="faint small">{note}</p> : null}
    </div>
  );
}

function statusOf(exchange: Exchange | undefined): "ok" | "error" | null {
  if (!exchange || exchange.status == null) return null;
  return exchange.status >= 500 ? "error" : "ok"; // the services mark a server span ERROR on a 5xx only
}

/** Whether the service recorded this request: it answered with a traceresponse. */
export function recorded(exchange: Exchange | undefined): boolean {
  return Boolean(exchange?.received);
}

export function queryTrace(run: Run): { traceId: string | null; rows: SpanRow[] } {
  const e = run.exchange;
  const on = recorded(e) ? true : ("off" as const);
  return {
    traceId: e?.received?.traceId ?? e?.sent?.traceId ?? null,
    rows: [
      { depth: 0, op: "browser request", service: "this page", attrs: "started the trace (traceparent sent)", spanId: e?.sent?.spanId ?? null },
      {
        depth: 1,
        op: "POST /query",
        service: "adaptiverag-api",
        attrs: e?.status != null ? `HTTP ${e.status}` : undefined,
        spanId: e?.received?.spanId,
        duration: e?.ms,
        status: statusOf(e),
        exported: on,
      },
    ],
  };
}

/** The spans the orchestrator and its two services export for one job, as far as the responses describe them. */
export function jobRows(run: Run): SpanRow[] {
  const r = run.response;
  const answer = run.turn?.answer ?? null;
  const rows: SpanRow[] = [
    {
      depth: 0,
      op: "orchestrator.execute",
      service: "adaptiverag-orchestrator",
      attrs: r ? `job.status=${r.status}${r.turn_index != null ? ` · session.turn_index=${r.turn_index}` : ""}${r.answer_status ? ` · answer_status=${r.answer_status}` : ""}` : undefined,
      status: r ? (r.status === "failed" ? "error" : "ok") : null,
      exported: true,
    },
  ];
  if (!r || r.status !== "completed" || r.resolution?.kind === "unresolved") return rows;
  rows.push({ depth: 1, op: "POST /retrieve → retrieval.execute", service: "adaptiverag-retrieval", attrs: "one per retrieval the controllers make; count not returned by the API", exported: true });
  if (answer) {
    (answer.phase1 ?? []).forEach((part) => {
      rows.push({ depth: 1, op: "POST /generate → generation.execute", service: "adaptiverag-generation", attrs: `generation.status=${part.status}${part.abstention_reason ? ` · ${part.abstention_reason}` : ""}`, status: "ok", exported: true });
    });
  } else {
    rows.push({ depth: 1, op: "POST /generate → generation.execute", service: "adaptiverag-generation", attrs: "one per model answer", exported: true });
  }
  return rows;
}

export function inspectionTrace(inspection: Inspection): { traceId: string | null; rows: SpanRow[] } {
  const e = inspection.exchange;
  const result = inspection.result;
  const on = recorded(e) ? true : ("off" as const);
  const rows: SpanRow[] = [
    { depth: 0, op: "browser request", service: "this page", attrs: "started the trace (traceparent sent)", spanId: e?.sent?.spanId ?? null },
    { depth: 1, op: "POST /retrieve", service: "adaptiverag-retrieval", attrs: e?.status != null ? `HTTP ${e.status}` : undefined, spanId: e?.received?.spanId, duration: e?.ms, status: statusOf(e), exported: on },
  ];
  if (result) {
    rows.push({
      depth: 2,
      op: "retrieval.execute",
      service: "adaptiverag-retrieval",
      attrs: `retrieval.mode=${result.mode} · evidence_count=${result.evidence.length}${result.trace ? ` · rounds=${result.trace.rounds.length} · stop_reason=${result.trace.stop_reason}` : ""}`,
      status: "ok",
      exported: on,
    });
    for (const round of result.trace?.rounds ?? []) {
      rows.push({
        depth: 3,
        op: `retrieval.round #${round.iteration}`,
        service: "adaptiverag-retrieval",
        attrs: `strategy=${round.strategy} · retrieved=${round.retrieved.length} · new=${round.new.length} · coverage=${round.coverage.decision} · ${round.decision.split(":")[0]}`,
        status: "ok",
        exported: on,
      });
    }
  }
  return { traceId: e?.received?.traceId ?? e?.sent?.traceId ?? null, rows };
}
