// How the system is built. Every component named here exists in the repository (services/, adaptive/, docs/).

import type { ReactNode } from "react";
import { Section } from "../components/ui";

/** The path of one query, top to bottom. `via` is what carries it to the next step. */
const PATH: { name: string; where: string; text: string; via?: string }[] = [
  { name: "User", where: "this page", text: "A question and a session id.", via: "POST /query" },
  { name: "API", where: "services/api · :8000", text: "Validates the request, submits it as a job, waits for the job's final event, returns the stored turn.", via: "job message · RabbitMQ" },
  { name: "Orchestrator", where: "services/orchestrator", text: "Consumes the job, loads the session, runs the controllers, stores the turn. Calls retrieval and generation over HTTP.", via: "controllers" },
  { name: "Session and intent", where: "adaptive/session · adaptive/multi", text: "A follow-up is resolved against earlier turns and rewritten into a standalone query. A multi-part question is split into intents. Rules, no model.", via: "POST /retrieve · per query" },
  { name: "Retrieval", where: "services/retrieval · :8001", text: "Dense + BM25 fused by reciprocal rank, temporal version resolution, cross-encoder rerank. In iterative mode: bounded rounds until coverage is sufficient.", via: "ranked chunks" },
  { name: "Evidence", where: "adaptive/multi · adaptive/controller", text: "The evidence of the intents is fused into one context or kept per intent; answers are split by document version when versions disagree.", via: "POST /generate · per answer" },
  { name: "Generation", where: "services/generation · :8002", text: "Relevance gate, grounded prompt over labelled sources, model call, then a verifier that checks every claim's quotes against the source text.", via: "stored turn · Redis" },
  { name: "Answer", where: "QueryResponse", text: "Text with citations, or an abstention. Insufficient evidence is an answer status, not an error." },
];

const AROUND: [string, string, string][] = [
  ["RabbitMQ", "adaptiverag.jobs · job_events", "Carries each job from the API to the orchestrator, and its events back."],
  ["Redis", "session store", "Holds the conversation. The orchestrator writes each turn; the API reads the answered turn from it."],
  ["PostgreSQL", "services/persistence", "A copy of the document and chunk metadata, behind the MCP database_query tool's named queries."],
  ["MCP server", "services/mcp · :8003", "Three read-only tools over the same retrieval and metadata: document_search, metadata_lookup, database_query."],
  ["OpenTelemetry", "services/telemetry", "A span for every service boundary and every retrieval round; correlation ids on every span; no payload recorded."],
];

const RETRIEVAL: [string, string][] = [
  ["Dense + BM25", "Two retrievers over the chunked corpus (the project's own arctic-m dense index and BM25), fused by reciprocal rank."],
  ["Temporal resolution", "Selects the document version the question asks about (a date, or “currently”) and drops the others."],
  ["Rerank", "A cross-encoder scores each candidate against the question."],
  ["Coverage", "Sufficient when every organization the question names has a relevant source in the context."],
  ["Iterative loop", "Retrieve, judge coverage, refine the query, at most a fixed number of rounds. No model in the loop."],
];

const GENERATION: [string, string][] = [
  ["Relevance gate", "Abstains without a model call when no source is relevant enough."],
  ["Grounded prompt", "The model sees labelled sources (S1, S2, …) and must quote what it uses."],
  ["Verifier", "Each claim's quotes are checked against the source text; an unverified answer is rejected."],
  ["Abstention", "Insufficient evidence is an answer status, not an error."],
];

function Facts({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="kvs">
      {rows.map(([k, v]) => (
        <div className="kv" key={k}>
          <dt>{k}</dt>
          <dd className="muted">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ArchitectureView({ menu }: { menu: ReactNode }) {
  return (
    <div className="page">
      <div className="page-inner">
        <header className="page-head">
          {menu}
          <div>
            <h1>Architecture</h1>
            <p>How a query travels through the system, and what each part is responsible for.</p>
          </div>
        </header>

        <div className="arch">
          <Section title="The path of a query">
            <ol className="flow">
              {PATH.map((step) => (
                <li key={step.name}>
                  <div className="flow-node">
                    <div className="flow-name">
                      <strong>{step.name}</strong>
                      <span className="mono faint">{step.where}</span>
                    </div>
                    <p>{step.text}</p>
                  </div>
                  {step.via ? <div className="flow-via">{step.via}</div> : null}
                </li>
              ))}
            </ol>
          </Section>

          <Section title="Supporting infrastructure">
            <ul className="infra">
              {AROUND.map(([name, where, text]) => (
                <li key={name}>
                  <strong>{name}</strong>
                  <span className="mono faint">{where}</span>
                  <p>{text}</p>
                </li>
              ))}
            </ul>
            <p className="faint small">
              There is no separate vector database: the dense index is part of the retrieval service. Trace context crosses the HTTP calls but not
              RabbitMQ; the job id joins the two traces.
            </p>
          </Section>
        </div>

        <div className="arch">
          <Section title="Inside retrieval">
            <Facts rows={RETRIEVAL} />
          </Section>
          <Section title="Inside generation">
            <Facts rows={GENERATION} />
          </Section>
        </div>
      </div>
    </div>
  );
}
