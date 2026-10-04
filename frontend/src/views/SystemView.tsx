// The running system: what each service's /health reports, and what this interface cannot read. No figure here is
// derived or estimated.

import type { ReactNode } from "react";
import type { ServiceName } from "../api/types";
import { Note, Section, StatusBadge } from "../components/ui";
import { clock, ms } from "../lib/model";
import { SERVICES, useStore, type ServiceHealth } from "../state/store";

const ROLE: Record<ServiceName, string> = {
  api: "Public HTTP interface: validates a query, submits it as a job, returns the stored turn",
  retrieval: "Hybrid retrieval, temporal version resolution, rerank, and the bounded iterative loop",
  generation: "Grounded answerer: relevance gate, prompt, model call, claim verifier",
  mcp: "Model Context Protocol server: document_search, metadata_lookup, database_query",
};

function reported(service: ServiceName, s: ServiceHealth): ReactNode {
  const h = s.health;
  if (!h) return <span className="faint">{s.state === "checking" ? "checking" : (s.error ?? "no answer")}</span>;
  if (service === "retrieval") return `${h.chunks} chunks indexed · reranker ${h.reranker ? "loaded" : "not loaded"}`;
  if (service === "generation") return `provider ${h.provider} · model ${h.model}`;
  if (service === "mcp") return `${h.documents} documents · ${h.chunks} chunks · database adapter ${h.database} · search ${h.retrieval}`;
  return "accepting queries";
}

export function SystemView({ menu }: { menu: ReactNode }) {
  const { health, checkHealth, exchanges } = useStore();
  const lastApi = [...exchanges].reverse().find((e) => e.service === "api" && e.sent);
  const generation = health.generation.health;
  return (
    <div className="page">
      <div className="page-inner">
        <header className="page-head">
          {menu}
          <div>
            <h1>System</h1>
            <p>What each service reports about itself, and what the backend does not report.</p>
          </div>
          <button className="btn" onClick={checkHealth}>
            Check services
          </button>
        </header>

        <Section title="Services" aside="from each service's GET /health">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Service</th>
                  <th>State</th>
                  <th>Reported by the service</th>
                  <th>Address</th>
                  <th>Response</th>
                  <th>Checked</th>
                </tr>
              </thead>
              <tbody>
                {SERVICES.map((service) => {
                  const s = health[service];
                  return (
                    <tr key={service}>
                      <td>
                        <strong>{service}</strong>
                        <div className="muted small">{ROLE[service]}</div>
                      </td>
                      <td>
                        <StatusBadge tone={s.state === "ok" ? "ok" : s.state === "down" ? "err" : "neutral"}>
                          {s.state === "ok" ? "healthy" : s.state === "down" ? "unreachable" : "checking"}
                        </StatusBadge>
                      </td>
                      <td>{reported(service, s)}</td>
                      <td className="mono">{__SERVICE_TARGETS__[service]}</td>
                      <td className="mono">{s.state === "ok" ? ms(s.ms) : "—"}</td>
                      <td className="mono">{s.checkedAt ? clock(s.checkedAt) : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {generation?.provider === "offline-stub" ? (
            <Note tone="warn">
              <strong>The generation service runs its stub model.</strong> Retrieval, the relevance gate and the verifier are real, but the stub always
              reports insufficient evidence, so every answer is an abstention. Start the generation service with a model provider to get answers.
            </Note>
          ) : null}
          {health.mcp.state === "down" ? <p className="faint small">The MCP server is optional for this interface: nothing here calls its tools.</p> : null}
        </Section>

        <Section title="What the backend does not report">
          <dl className="kvs">
            <div className="kv">
              <dt>Orchestrator</dt>
              <dd className="muted">It has no HTTP interface. A query that completes shows it is consuming jobs; nothing else reports its state or its retrieval mode.</dd>
            </div>
            <div className="kv">
              <dt>RabbitMQ and Redis</dt>
              <dd className="muted">No health endpoint reports them. The API needs both to start and to answer.</dd>
            </div>
            <div className="kv">
              <dt>PostgreSQL</dt>
              <dd className="muted">The MCP server reports which database adapter it was started with, not whether the database answers.</dd>
            </div>
            <div className="kv">
              <dt>Tracing</dt>
              <dd className="muted">
                {lastApi
                  ? lastApi.received
                    ? "The API answered the last traced request with a traceresponse header: it is recording spans."
                    : "The API answered the last traced request without a traceresponse header: it is not recording spans."
                  : "Known after the first query: a service that records spans answers with a traceresponse header."}
              </dd>
            </div>
            <div className="kv">
              <dt>Metrics</dt>
              <dd className="muted">The system exposes no accuracy, throughput or usage metrics, so none are shown.</dd>
            </div>
            <div className="kv">
              <dt>Documents</dt>
              <dd className="muted">No service returns a document file or a page image. A source is shown as its indexed chunk text, its metadata and its origin URL.</dd>
            </div>
          </dl>
        </Section>
      </div>
    </div>
  );
}
