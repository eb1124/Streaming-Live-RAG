// W3C trace context. The browser is the first hop of a trace: it sends a `traceparent` it made, and the service
// answers with `traceresponse` (the ids of its own server span) when it is recording spans (services/telemetry).

export interface TraceIds {
  traceId: string;
  spanId: string;
}

const HEADER = /^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$/;

function hex(bytes: number): string {
  const buffer = new Uint8Array(bytes);
  crypto.getRandomValues(buffer);
  return Array.from(buffer, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** A new trace started by this browser: random ids, as the specification requires; sampled. */
export function newTraceparent(): string {
  return `00-${hex(16)}-${hex(8)}-01`;
}

export function parseTraceHeader(value: string | null | undefined): TraceIds | null {
  const match = value ? HEADER.exec(value.trim()) : null;
  return match ? { traceId: match[1], spanId: match[2] } : null;
}
