// Telemetry of one turn: the identifiers that tie its requests and spans together, the requests this page made,
// and the traces as far as responses describe them (see components/TraceTree.tsx for what is first-hand).
// Two things are kept apart: the application's own execution state (the RetrievalTrace) and observability
// (OpenTelemetry). The first is in the responses; the second is exported by the services.

import type { Exchange } from "../../api/client";
import { clock, count, inspectionsOf, ms, outcomeOf, type Run } from "../../lib/model";
import { useStore } from "../../state/store";
import { TraceTree, inspectionTrace, jobRows, queryTrace, recorded } from "../TraceTree";
import { Id, MetricRow, Metrics, Note, StatusBadge } from "../ui";
import { Frame } from "./Frame";
import { InspectorSection } from "./parts";

function Recording({ exchange }: { exchange: Exchange | undefined }) {
  if (!exchange || exchange.status == null) return <StatusBadge>no response</StatusBadge>;
  return recorded(exchange) ? <StatusBadge tone="ok">recording spans</StatusBadge> : <StatusBadge>not recording</StatusBadge>;
}

function ExchangeRow({ label, exchange }: { label: string; exchange: Exchange }) {
  return (
    <div className="exchange">
      <div className="exchange-line">
        <span className="mono op">
          {exchange.method} {label}
        </span>
        <span className="mono">{exchange.status ?? "—"}</span>
        <span className="mono">{ms(exchange.ms)}</span>
      </div>
      <div className="exchange-meta">
        {exchange.service} · {clock(exchange.startedAt)} · trace {exchange.sent ? <Id>{exchange.sent.traceId}</Id> : "none sent"} · server span{" "}
        {exchange.received ? <Id>{exchange.received.spanId}</Id> : "not recorded"}
      </div>
    </div>
  );
}

export function TelemetryInspector({ run }: { run: Run }) {
  const { openPanel } = useStore();
  const r = run.response;
  const outcome = outcomeOf(run);

  if (run.stored) {
    return (
      <Frame kind="Telemetry" title={run.question}>
        <Note>
          This turn was read from the stored session. This page did not send its <span className="mono">POST /query</span>, so it holds no request id,
          job id, timing or trace context for it, and the stored session does not record them.
        </Note>
        <InspectorSection title="Application correlation">
          <Metrics>
            <MetricRow label="session_id">
              <Id>{run.sessionId}</Id>
            </MetricRow>
            <MetricRow label="turn_index">{run.turn?.index}</MetricRow>
            <MetricRow label="answer status">{outcome?.status}</MetricRow>
          </Metrics>
        </InspectorSection>
        <Requests run={run} />
      </Frame>
    );
  }

  const inspections = inspectionsOf(run).filter((i) => i.exchange);
  const traced = inspectionsOf(run).filter((i) => i.result?.trace);
  const query = queryTrace(run);
  return (
    <Frame kind="Telemetry" title={run.question} subtitle="Correlation ids, trace context and the requests behind this turn.">
      <InspectorSection title="Turn">
        <Metrics>
          <MetricRow label="Job status">{r?.status ?? "no response"}</MetricRow>
          <MetricRow label="Answer status">{r?.answer_status}</MetricRow>
          <MetricRow label="Latency">
            {run.exchange ? (
              <>
                <span className="mono">{ms(run.exchange.ms)}</span> <span className="faint">POST /query round trip, measured in this browser</span>
              </>
            ) : null}
          </MetricRow>
          <MetricRow label="Evidence count">{r ? r.evidence.length : null}</MetricRow>
        </Metrics>
      </InspectorSection>

      <InspectorSection title="Application correlation" aside="travels in the contracts">
        <Metrics>
          <MetricRow label="correlation_id">{r?.job_id ? <Id>{r.job_id}</Id> : null}</MetricRow>
          <MetricRow label="job_id">{r?.job_id ? <Id>{r.job_id}</Id> : null}</MetricRow>
          <MetricRow label="request_id">{r?.request_id || run.requestId ? <Id>{r?.request_id ?? run.requestId}</Id> : null}</MetricRow>
          <MetricRow label="session_id">
            <Id>{run.sessionId}</Id>
          </MetricRow>
          <MetricRow label="turn_index">{r?.turn_index}</MetricRow>
        </Metrics>
        <p className="faint small">
          Every span of the job carries <span className="mono">correlation_id</span> = the job id, in every service. It is what joins the traces below.
        </p>
      </InspectorSection>

      <InspectorSection title="Retrieval trace" aside="application state, not telemetry">
        {traced.length ? (
          <Metrics>
            {traced.map((i) => (
              <MetricRow key={i.key} label={i.label}>
                {count(i.result!.trace!.rounds.length, "round")} · stop reason {i.result!.trace!.stop_reason.replace(/_/g, " ")} · {count(i.result!.evidence.length, "chunk")}
              </MetricRow>
            ))}
          </Metrics>
        ) : (
          <p className="muted small">No iterative retrieval of this page is loaded for this turn, so there are no rounds or stop reason to show.</p>
        )}
        <p className="faint small">The RetrievalTrace of this page's own retrieval request. The orchestrator's is not returned by the API.</p>
        <button className="link" onClick={() => openPanel({ kind: "execution", runId: run.id }, true)}>
          Open the execution of this turn
        </button>
      </InspectorSection>

      <InspectorSection title="OpenTelemetry trace context" aside="travels in HTTP headers">
        <Metrics>
          <MetricRow label="API">
            <Recording exchange={run.exchange} />
          </MetricRow>
          <MetricRow label="Trace id">{run.exchange?.sent ? <Id>{run.exchange.sent.traceId}</Id> : null}</MetricRow>
          <MetricRow label="Parent span sent">{run.exchange?.sent ? <Id>{run.exchange.sent.spanId}</Id> : null}</MetricRow>
          <MetricRow label="API server span">{run.exchange?.received ? <Id>{run.exchange.received.spanId}</Id> : <span className="faint">not recorded</span>}</MetricRow>
        </Metrics>
        <p className="faint small">
          {recorded(run.exchange)
            ? "The API answered with a traceresponse header: it recorded a server span in the trace this page started."
            : "The API sent no traceresponse header: it is running without a trace exporter (the default), so it recorded no span. The ids above are the context this page sent."}
        </p>
      </InspectorSection>

      <Requests run={run} />

      <InspectorSection title="Span tree">
        <TraceTree title="Trace 1 · the query request" traceId={query.traceId} rows={query.rows} badge={<Recording exchange={run.exchange} />} />
        <div className="boundary">RabbitMQ: no trace context · joined by correlation_id</div>
        <TraceTree
          title="Trace 2 · the job in the orchestrator"
          traceId={null}
          rows={jobRows(run)}
          note={
            <>
              The orchestrator starts its own trace for each job; its calls to retrieval and generation continue it. Its ids exist only in the
              exporter, and the API does not report whether it is recording. To find these spans in a trace backend, search for{" "}
              <span className="mono">correlation_id = {r?.job_id ?? "the job id"}</span>.
            </>
          }
        />
        {inspections.length ? <div className="boundary">This page's retrieval requests: each its own trace</div> : null}
        {inspections.map((i) => {
          const t = inspectionTrace(i);
          return <TraceTree key={i.key} title={`Retrieval · ${i.label.toLowerCase()}`} traceId={t.traceId} rows={t.rows} badge={<Recording exchange={i.exchange} />} />;
        })}
        <p className="faint small">
          Span durations are in the exporter; the times here are round trips measured in this browser. Spans never contain the question, the answer,
          chunk text or chunk ids.
        </p>
      </InspectorSection>
    </Frame>
  );
}

function Requests({ run }: { run: Run }) {
  const inspections = inspectionsOf(run).filter((i) => i.exchange);
  if (!run.exchange && !run.turnExchange && !inspections.length) return null;
  return (
    <InspectorSection title="Requests this page made" aside="browser round trips">
      <div className="exchanges">
        {run.exchange ? <ExchangeRow label="/query" exchange={run.exchange} /> : null}
        {run.turnExchange ? <ExchangeRow label="/sessions/{session_id}" exchange={run.turnExchange} /> : null}
        {inspections.map((i) => (
          <ExchangeRow key={i.key} label={`/retrieve (${i.label.toLowerCase()})`} exchange={i.exchange!} />
        ))}
      </div>
    </InspectorSection>
  );
}
