/// <reference types="vite/client" />

/** The proxy targets of vite.config.ts: where /svc/<name> is forwarded. */
declare const __SERVICE_TARGETS__: Record<"api" | "retrieval" | "generation" | "mcp", string>;
