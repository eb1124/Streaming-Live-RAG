// The only place that talks to the backend. Every call goes through `request`: one base path per service (proxied
// by the dev server, see vite.config.ts), one error type, and the facts of the exchange the UI shows (HTTP status,
// time measured in this browser, the trace context sent and the one the service answered with).

import { newTraceparent, parseTraceHeader, type TraceIds } from "../lib/trace";
import type {
  Health,
  QueryRequest,
  QueryResponse,
  RetrievalRequest,
  RetrievalResponse,
  ServiceName,
  SessionDetail,
} from "./types";

export const SERVICE_PATH: Record<ServiceName, string> = {
  api: "/svc/api",
  retrieval: "/svc/retrieval",
  generation: "/svc/generation",
  mcp: "/svc/mcp",
};

/** What the UI records about one HTTP exchange. `ms` is wall time in this browser, proxy included. */
export interface Exchange {
  method: string;
  service: ServiceName;
  path: string;
  status: number | null;
  ms: number;
  startedAt: string;
  sent: TraceIds | null; // the traceparent this browser sent
  received: TraceIds | null; // the service's traceresponse: present only while it records spans
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly exchange: Exchange,
    readonly detail: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface Result<T> {
  data: T;
  exchange: Exchange;
}

function describe(status: number, detail: unknown): string {
  if (Array.isArray(detail)) {
    // FastAPI's validation errors: [{loc, msg, type}]
    const fields = detail.map((d) => `${(d?.loc ?? []).slice(1).join(".") || "body"}: ${d?.msg ?? "invalid"}`);
    return `The request was rejected (HTTP ${status}): ${fields.join("; ")}`;
  }
  if (typeof detail === "string" && detail) return `HTTP ${status}: ${detail}`;
  return `HTTP ${status}`;
}

async function request<T>(
  service: ServiceName,
  path: string,
  init: { method?: string; body?: unknown; accept?: number[]; trace?: boolean } = {},
): Promise<Result<T>> {
  const method = init.method ?? "GET";
  const traceparent = init.trace === false ? null : newTraceparent();
  const headers: Record<string, string> = { accept: "application/json" };
  if (init.body !== undefined) headers["content-type"] = "application/json";
  if (traceparent) headers.traceparent = traceparent;
  const exchange: Exchange = {
    method,
    service,
    path,
    status: null,
    ms: 0,
    startedAt: new Date().toISOString(),
    sent: parseTraceHeader(traceparent),
    received: null,
  };
  const started = performance.now();
  let response: Response;
  try {
    response = await fetch(SERVICE_PATH[service] + path, {
      method,
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
    });
  } catch (cause) {
    exchange.ms = performance.now() - started;
    throw new ApiError(`The ${service} service could not be reached (${String((cause as Error)?.message ?? cause)}).`, exchange);
  }
  exchange.ms = performance.now() - started;
  exchange.status = response.status;
  exchange.received = parseTraceHeader(response.headers.get("traceresponse"));
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (response.ok || init.accept?.includes(response.status)) {
    if (body === null) throw new ApiError(`The ${service} service answered HTTP ${response.status} without JSON.`, exchange);
    return { data: body as T, exchange };
  }
  const detail = (body as { detail?: unknown } | null)?.detail ?? null;
  // The dev proxy answers 500/502/504 itself when the target is down.
  const down = body === null && response.status >= 500;
  throw new ApiError(
    down ? `The ${service} service is not reachable through the proxy (HTTP ${response.status}).` : describe(response.status, detail),
    exchange,
    detail,
  );
}

export const backend = {
  /** GET /health of one service. Not traced by the services, so no trace context is sent. */
  health: (service: ServiceName) => request<Health>(service, "/health", { trace: false }),

  /** POST /query. A failed job is HTTP 502 with a QueryResponse body (status "failed"): data, not an exception. */
  query: (body: QueryRequest) => request<QueryResponse>("api", "/query", { method: "POST", body, accept: [502] }),

  /** GET /sessions/{id}: the stored session (phase 10B). */
  session: (sessionId: string) => request<SessionDetail>("api", `/sessions/${encodeURIComponent(sessionId)}`),

  /** POST /retrieve on the retrieval service: evidence with its text, and the RetrievalTrace in iterative mode. */
  retrieve: (body: RetrievalRequest) => request<RetrievalResponse>("retrieval", "/retrieve", { method: "POST", body }),
};
