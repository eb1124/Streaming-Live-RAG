// The left region: the sessions this browser started (kept locally; their content always comes from
// GET /sessions/{id}), and the little navigation there is.

import { Moon, Network, Plus, Server, Sparkles, Sun } from "lucide-react";
import { useState } from "react";
import { count, thread } from "../lib/model";
import { SERVICES, useStore } from "../state/store";

/** One line about the backend, from the four /health answers only. */
function BackendStatus() {
  const { health, navigate } = useStore();
  const states = SERVICES.map((s) => health[s].state);
  const up = states.filter((s) => s === "ok").length;
  let tone = "ok";
  let text = "All services reachable";
  if (states.every((s) => s === "checking")) {
    tone = "checking";
    text = "Checking services";
  } else if (health.api.state === "down") {
    tone = "down";
    text = "API unreachable";
  } else if (up < SERVICES.length) {
    tone = "checking";
    text = `${up} of ${SERVICES.length} services reachable`;
  }
  return (
    <button className="status-line" onClick={() => navigate("system")} title="Open the system view">
      <span className={`status-dot ${tone}`} aria-hidden />
      {text}
    </button>
  );
}

export function SessionPanel({ onNavigate }: { onNavigate?: () => void }) {
  const { view, navigate, theme, toggleTheme, sessionId, openSession, newSession, knownSessions, sessionTitles, sessions, runs, busy } = useStore();
  const [lookup, setLookup] = useState("");
  const valid = /^[A-Za-z0-9_.:-]{1,128}$/.test(lookup);
  const isNew = !knownSessions.includes(sessionId);

  function go(action: () => void) {
    action();
    navigate("workspace");
    onNavigate?.();
  }

  return (
    <nav className="left" aria-label="Sessions and views">
      <div className="brand">
        <span className="brand-mark" aria-hidden>
          <Sparkles />
        </span>
        <div className="brand-text">
          <strong>
            <span className="accent">Streaming</span>LiveRAG
          </strong>
          <span>Adaptive RAG pipeline</span>
        </div>
      </div>

      <button className="btn block" onClick={() => go(() => newSession())} disabled={busy}>
        <Plus aria-hidden /> New session
      </button>

      <div className="left-label">Sessions</div>
      <div className="session-list">
        {isNew ? (
          <button className="session on" aria-current={view === "workspace" ? "true" : undefined} onClick={() => go(() => undefined)}>
            <span className="title">{thread(runs, sessionId)[0]?.question ?? "New session"}</span>
            <span className="meta mono">{sessionId}</span>
          </button>
        ) : null}
        {knownSessions.map((id) => {
          const turns = sessions[id]?.detail?.turns.length;
          return (
            <button
              key={id}
              className={`session ${id === sessionId ? "on" : ""}`}
              aria-current={id === sessionId && view === "workspace" ? "true" : undefined}
              onClick={() => go(() => (id === sessionId ? undefined : openSession(id)))}
              disabled={busy && id !== sessionId}
            >
              <span className="title">{sessionTitles[id] ?? id}</span>
              <span className="meta mono">
                {id}
                {turns != null ? ` · ${count(turns, "turn")}` : ""}
              </span>
            </button>
          );
        })}
        {!knownSessions.length && !isNew ? <p className="faint small">None yet.</p> : null}
      </div>

      <form
        className="lookup"
        onSubmit={(e) => {
          e.preventDefault();
          if (!valid) return;
          go(() => openSession(lookup));
          setLookup("");
        }}
      >
        <input className="input mono" aria-label="Open a session by id" placeholder="Open a session by id" value={lookup} onChange={(e) => setLookup(e.target.value.trim())} />
      </form>

      <div className="left-foot">
        <button className="nav-item" aria-current={view === "architecture" ? "page" : undefined} onClick={() => { navigate("architecture"); onNavigate?.(); }}>
          <Network aria-hidden /> Architecture
        </button>
        <button className="nav-item" aria-current={view === "system" ? "page" : undefined} onClick={() => { navigate("system"); onNavigate?.(); }}>
          <Server aria-hidden /> System
        </button>
        <div className="left-status">
          <BackendStatus />
          <button
            className="icon-btn"
            onClick={toggleTheme}
            title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          >
            {theme === "dark" ? <Sun aria-hidden /> : <Moon aria-hidden />}
          </button>
        </div>
      </div>
    </nav>
  );
}
