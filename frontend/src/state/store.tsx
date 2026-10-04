// Application state: the turns of the sessions this page has open (asked here, or read from the stored session),
// the services' health, the sessions this browser started, the theme, the current view and the inspector.
// Everything about a turn comes from the backend's responses; this module only sequences the requests (query, then
// the stored turn, then the retrievals that return the evidence text).

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, backend, type Exchange } from "../api/client";
import type { Health, RetrievalMode, RetrievalResponse, ServiceName, SessionDetail } from "../api/types";
import { ORCHESTRATOR_K, inspectionTargets, outcomeOf, targetsFor, turnIndexOf, type Inspection, type Run } from "../lib/model";

export type View = "workspace" | "architecture" | "system";
export const VIEWS: View[] = ["workspace", "architecture", "system"];
export const SERVICES: ServiceName[] = ["api", "retrieval", "generation", "mcp"];

/** What the inspector shows. Every panel belongs to one run. */
export type Panel =
  | { kind: "source"; runId: string; chunkId: string; citation: number }
  | { kind: "evidence"; runId: string; chunkId: string }
  | { kind: "execution"; runId: string }
  | { kind: "intent"; runId: string; index: number }
  | { kind: "telemetry"; runId: string };

export interface ServiceHealth {
  state: "checking" | "ok" | "down";
  health?: Health;
  error?: string;
  ms?: number;
  checkedAt?: string;
}

/** How the page asks POST /retrieve again for a turn's queries, to read the evidence text and the rounds. */
export interface InspectOptions {
  mode: RetrievalMode;
  maxRounds: number;
}

export interface Settings extends InspectOptions {
  auto: boolean; // load the evidence after each answer
}

export interface SessionEntry {
  loading: boolean;
  detail?: SessionDetail;
  error?: string;
  notFound?: boolean;
}

interface Store {
  view: View;
  navigate: (view: View) => void;
  theme: "dark" | "light";
  toggleTheme: () => void;
  health: Record<ServiceName, ServiceHealth>;
  checkHealth: () => void;
  runs: Run[];
  runById: (id: string) => Run | null;
  busy: boolean; // a query is in flight (the api handles one query at a time)
  ask: (question: string) => Promise<void>;
  inspect: (runId: string, key: string, label: string, query: string, options?: InspectOptions) => Promise<RetrievalResponse | null>;
  loadEvidence: (runId: string) => Promise<void>;
  settings: Settings;
  setSettings: (settings: Settings) => void;
  sessionId: string; // the session the workspace shows and the composer continues
  openSession: (id: string) => void;
  newSession: () => string;
  knownSessions: string[];
  sessionTitles: Record<string, string>; // a session's first question, once the backend returned it
  sessions: Record<string, SessionEntry>;
  loadSession: (id: string) => Promise<void>;
  exchanges: Exchange[]; // every traced request this tab made, newest last
  panel: Panel | null;
  canGoBack: boolean;
  openPanel: (panel: Panel, push?: boolean) => void;
  backPanel: () => void;
  closePanel: () => void;
}

const Context = createContext<Store | null>(null);

function token(length = 6): string {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => (b % 36).toString(36)).join("");
}

function freshSessionId(): string {
  const d = new Date();
  const day = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
  return `demo-${day}-${token()}`;
}

function stored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function persist(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable: the app works without it */
  }
}

function viewFromHash(): View {
  const name = window.location.hash.replace(/^#\/?/, "") as View;
  return VIEWS.includes(name) ? name : "workspace";
}

function message(error: unknown): string {
  return error instanceof ApiError ? error.message : String((error as Error)?.message ?? error);
}

const DEFAULT_SETTINGS: Settings = { auto: true, mode: "iterative", maxRounds: 3 };

/** The runs with a stored session merged in: a run this page asked gets its stored turn; a turn it did not ask
 *  becomes a run of its own, marked `stored`. */
function adopt(runs: Run[], sessionId: string, detail: SessionDetail): Run[] {
  const next = runs.map((run) => {
    const index = run.sessionId === sessionId ? turnIndexOf(run) : null;
    const turn = index == null ? undefined : detail.turns.find((t) => t.index === index);
    return turn ? { ...run, turn, turnError: undefined } : run;
  });
  for (const turn of detail.turns) {
    if (next.some((run) => run.sessionId === sessionId && turnIndexOf(run) === turn.index)) continue;
    next.push({ id: `stored:${sessionId}:${turn.index}`, question: turn.question, sessionId, pending: false, stored: true, turn, inspections: {} });
  }
  return next;
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [view, setView] = useState<View>(viewFromHash);
  const [theme, setTheme] = useState<"dark" | "light">(() =>
    document.documentElement.dataset.theme === "light" ? "light" : "dark",
  );
  const [health, setHealth] = useState<Record<ServiceName, ServiceHealth>>(
    () => Object.fromEntries(SERVICES.map((s) => [s, { state: "checking" }])) as Record<ServiceName, ServiceHealth>,
  );
  const [runs, setRuns] = useState<Run[]>([]);
  const [knownSessions, setKnownSessions] = useState<string[]>(() => stored<string[]>("slr.sessions", []));
  const [sessionTitles, setSessionTitles] = useState<Record<string, string>>(() => stored<Record<string, string>>("slr.titles", {}));
  const [sessionId, setSessionIdState] = useState<string>(() => stored<string>("slr.session", "") || freshSessionId());
  const [sessions, setSessions] = useState<Record<string, SessionEntry>>({});
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [settings, setSettingsState] = useState<Settings>(() => ({ ...DEFAULT_SETTINGS, ...stored<Partial<Settings>>("slr.inspect", {}) }));
  const [panels, setPanels] = useState<Panel[]>([]);
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const latest = useRef({ runs, settings, sessionId });
  latest.current = { runs, settings, sessionId };

  useEffect(() => {
    const onHash = () => setView(viewFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const navigate = useCallback((next: View) => {
    window.location.hash = `/${next}`;
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme((current) => {
      const next = current === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try {
        localStorage.setItem("slr.theme", next);
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  const record = useCallback((exchange: Exchange) => setExchanges((all) => [...all, exchange]), []);
  const patchRun = useCallback((id: string, patch: (run: Run) => Run) => {
    setRuns((all) => all.map((r) => (r.id === id ? patch(r) : r)));
  }, []);

  const checkHealth = useCallback(() => {
    for (const service of SERVICES) {
      setHealth((h) => ({ ...h, [service]: { ...h[service], state: "checking" } }));
      backend
        .health(service)
        .then(({ data, exchange }) => {
          // Another program may answer on the port: only the service that names itself counts.
          const ok = data?.status === "ok" && data?.service === service;
          setHealth((h) => ({
            ...h,
            [service]: ok
              ? { state: "ok", health: data, ms: exchange.ms, checkedAt: exchange.startedAt }
              : { state: "down", error: "Something else answered on this address.", checkedAt: exchange.startedAt },
          }));
        })
        .catch((error) =>
          setHealth((h) => ({ ...h, [service]: { state: "down", error: message(error), checkedAt: new Date().toISOString() } })),
        );
    }
  }, []);

  useEffect(() => {
    checkHealth();
    const timer = window.setInterval(checkHealth, 30_000);
    return () => window.clearInterval(timer);
  }, [checkHealth]);

  const setSettings = useCallback((next: Settings) => {
    setSettingsState(next);
    persist("slr.inspect", next);
  }, []);

  const remember = useCallback((id: string) => {
    setKnownSessions((all) => {
      if (all.includes(id)) return all;
      const next = [id, ...all].slice(0, 30);
      persist("slr.sessions", next);
      return next;
    });
  }, []);

  /** A stored session, as the backend returned it: its turns join the runs, its first question names it. */
  const receive = useCallback(
    (id: string, detail: SessionDetail) => {
      setSessions((all) => ({ ...all, [id]: { loading: false, detail } }));
      setRuns((all) => adopt(all, id, detail));
      const first = detail.turns[0]?.question;
      if (first) {
        remember(id);
        setSessionTitles((all) => {
          if (all[id] === first) return all;
          const next = { ...all, [id]: first };
          persist("slr.titles", next);
          return next;
        });
      }
    },
    [remember],
  );

  const loadSession = useCallback(
    async (id: string) => {
      setSessions((all) => ({ ...all, [id]: { ...all[id], loading: true, error: undefined } }));
      try {
        const { data, exchange } = await backend.session(id);
        record(exchange);
        receive(id, data);
      } catch (error) {
        if (error instanceof ApiError) record(error.exchange);
        const notFound = error instanceof ApiError && error.exchange.status === 404;
        setSessions((all) => ({ ...all, [id]: { loading: false, notFound, error: notFound ? undefined : message(error) } }));
      }
    },
    [record, receive],
  );

  const openSession = useCallback(
    (id: string) => {
      setSessionIdState(id);
      persist("slr.session", id);
      setPanels([]);
      void loadSession(id);
    },
    [loadSession],
  );

  const newSession = useCallback(() => {
    const id = freshSessionId();
    setSessionIdState(id);
    persist("slr.session", id);
    setPanels([]);
    return id;
  }, []);

  // The session the page starts in may already hold turns (a reload): read it once. A session this browser has
  // not had answered yet is new, and the backend has nothing for it.
  useEffect(() => {
    const id = latest.current.sessionId;
    if (stored<string[]>("slr.sessions", []).includes(id)) void loadSession(id);
  }, [loadSession]);

  const inspect = useCallback(
    async (runId: string, key: string, label: string, query: string, options?: InspectOptions) => {
      const { mode, maxRounds } = options ?? latest.current.settings;
      const request = { query, mode, max_rounds: maxRounds, k: ORCHESTRATOR_K, request_id: `ui-inspect-${token()}` };
      const start: Inspection = { key, label, request, pending: true };
      patchRun(runId, (r) => ({ ...r, inspections: { ...r.inspections, [key]: start } }));
      try {
        const { data, exchange } = await backend.retrieve(request);
        record(exchange);
        patchRun(runId, (r) => ({ ...r, inspections: { ...r.inspections, [key]: { ...start, pending: false, result: data, exchange } } }));
        return data;
      } catch (error) {
        const exchange = error instanceof ApiError ? error.exchange : undefined;
        if (exchange) record(exchange);
        patchRun(runId, (r) => ({ ...r, inspections: { ...r.inspections, [key]: { ...start, pending: false, exchange, error: message(error) } } }));
        return null;
      }
    },
    [patchRun, record],
  );

  /** Ask the retrieval service again for the run's queries until the chunks the model was shown have their text:
   *  the whole question first, then only the intents whose stored record lists a chunk still missing. */
  const gather = useCallback(
    async (run: Run) => {
      const have = new Set<string>();
      for (const inspection of Object.values(run.inspections)) for (const e of inspection.result?.evidence ?? []) have.add(e.chunk.chunk_id);
      const fetch = async (key: string, label: string, query: string) => {
        if (run.inspections[key]?.result) return;
        const result = await inspect(run.id, key, label, query);
        for (const e of result?.evidence ?? []) have.add(e.chunk.chunk_id);
      };
      const whole = inspectionTargets(run).find((t) => t.key === "question");
      if (whole) await fetch(whole.key, whole.label, whole.query);
      const missing = (outcomeOf(run)?.evidence ?? []).filter((id) => !have.has(id));
      for (const target of targetsFor(run, missing)) await fetch(target.key, target.label, target.query);
    },
    [inspect],
  );

  const loadEvidence = useCallback(
    async (runId: string) => {
      const run = latest.current.runs.find((r) => r.id === runId);
      if (run) await gather(run);
    },
    [gather],
  );

  const ask = useCallback(
    async (question: string) => {
      if (pending.current) return;
      pending.current = true;
      setBusy(true);
      const session = latest.current.sessionId;
      const id = token(10);
      const requestId = `ui-${id}`;
      let run: Run = { id, question, sessionId: session, requestId, startedAt: new Date().toISOString(), pending: true, inspections: {} };
      setRuns((all) => [...all, run]);
      setPanels([]);
      let completed = false;
      try {
        const { data, exchange } = await backend.query({ session_id: session, question, request_id: requestId });
        record(exchange);
        run = { ...run, pending: false, response: data, exchange };
        // A stored copy of the same turn (the session was read while the query ran) gives way to the run.
        setRuns((all) =>
          all
            .filter((r) => !(r.stored && r.sessionId === session && data.turn_index != null && turnIndexOf(r) === data.turn_index))
            .map((r) => (r.id === id ? { ...r, pending: false, response: data, exchange } : r)),
        );
        if (data.status !== "completed" || data.turn_index == null) return;
        completed = true;
        remember(session);
        // The stored turn: the decomposition, per-intent results and claims that POST /query only summarises.
        try {
          const read = await backend.session(session);
          record(read.exchange);
          const turn = read.data.turns.find((t) => t.index === data.turn_index);
          run = turn ? { ...run, turn, turnExchange: read.exchange } : { ...run, turnExchange: read.exchange, turnError: `The stored session has no turn ${data.turn_index}.` };
          const { turnExchange, turnError } = run;
          patchRun(id, (r) => ({ ...r, turnExchange, turnError }));
          receive(session, read.data);
        } catch (error) {
          if (error instanceof ApiError) record(error.exchange);
          run = { ...run, turnError: message(error) };
          patchRun(id, (r) => ({ ...r, turnError: message(error) }));
        }
      } catch (error) {
        const exchange = error instanceof ApiError ? error.exchange : undefined;
        if (exchange) record(exchange);
        patchRun(id, (r) => ({ ...r, pending: false, exchange, error: message(error) }));
      } finally {
        pending.current = false;
        setBusy(false);
      }
      // The api is free again; the evidence text is read from the retrieval service in the background.
      if (completed && latest.current.settings.auto) await gather(run);
    },
    [gather, patchRun, receive, record, remember],
  );

  const openPanel = useCallback((panel: Panel, push = false) => setPanels((all) => (push ? [...all, panel] : [panel])), []);
  const backPanel = useCallback(() => setPanels((all) => all.slice(0, -1)), []);
  const closePanel = useCallback(() => setPanels([]), []);

  const value = useMemo<Store>(
    () => ({
      view,
      navigate,
      theme,
      toggleTheme,
      health,
      checkHealth,
      runs,
      runById: (id: string) => runs.find((r) => r.id === id) ?? null,
      busy,
      ask,
      inspect,
      loadEvidence,
      settings,
      setSettings,
      sessionId,
      openSession,
      newSession,
      knownSessions,
      sessionTitles,
      sessions,
      loadSession,
      exchanges,
      panel: panels[panels.length - 1] ?? null,
      canGoBack: panels.length > 1,
      openPanel,
      backPanel,
      closePanel,
    }),
    [view, navigate, theme, toggleTheme, health, checkHealth, runs, busy, ask, inspect, loadEvidence, settings, setSettings, sessionId, openSession, newSession, knownSessions, sessionTitles, sessions, loadSession, exchanges, panels, openPanel, backPanel, closePanel],
  );

  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useStore(): Store {
  const store = useContext(Context);
  if (!store) throw new Error("useStore outside StoreProvider");
  return store;
}
