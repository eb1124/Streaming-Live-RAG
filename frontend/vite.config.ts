/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The browser never calls a service directly: the dev (and preview) server proxies /svc/<name> to it, so the
// services need no CORS configuration. Targets: the repository's defaults (services/config.py), or .env.local.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "ADAPTIVERAG_");
  const targets = {
    api: env.ADAPTIVERAG_API_URL || "http://127.0.0.1:8000",
    retrieval: env.ADAPTIVERAG_RETRIEVAL_URL || "http://127.0.0.1:8001",
    generation: env.ADAPTIVERAG_GENERATION_URL || "http://127.0.0.1:8002",
    mcp: env.ADAPTIVERAG_MCP_URL || "http://127.0.0.1:8003",
  };
  const proxy = Object.fromEntries(
    Object.entries(targets).map(([name, target]) => [
      `/svc/${name}`,
      {
        target,
        changeOrigin: true,
        timeout: 200_000, // POST /query waits for the job (QUERY_TIMEOUT_S, default 180 s)
        proxyTimeout: 200_000,
        rewrite: (path: string) => path.replace(new RegExp(`^/svc/${name}`), ""),
      },
    ]),
  );
  const port = Number(env.ADAPTIVERAG_FRONTEND_PORT || 5180);
  return {
    plugins: [react()],
    define: { __SERVICE_TARGETS__: JSON.stringify(targets) },
    server: { port, proxy },
    preview: { port, proxy },
    test: { environment: "node", include: ["src/**/*.test.ts"] },
  };
});
