// The query input. POST /query takes a question and a session id, nothing else: the orchestrator's retrieval mode
// is its own setting (RETRIEVAL_MODE). The options here only decide how this page reads the evidence afterwards
// (POST /retrieve: its mode and round budget).

import { CornerDownLeft, SlidersHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { RetrievalMode } from "../api/types";
import { useStore } from "../state/store";

export function QueryComposer({ draft, onDraftUsed, followUp }: { draft: string | null; onDraftUsed: () => void; followUp?: { label: string; question: string; use: () => void } | null }) {
  const { ask, busy, health, settings, setSettings } = useStore();
  const [question, setQuestion] = useState("");
  const [options, setOptions] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    input.current?.focus();
  }, []);

  // An example chosen in the workspace arrives as a draft: it is put in the box, not sent.
  useEffect(() => {
    if (draft == null) return;
    setQuestion(draft);
    onDraftUsed();
    input.current?.focus();
  }, [draft, onDraftUsed]);

  const down = health.api.state === "down";
  const canRun = question.trim().length > 0 && !busy && !down;

  function run() {
    if (!canRun) return;
    void ask(question.trim());
    setQuestion("");
  }

  return (
    <div className="composer" data-busy={busy}>
      {followUp ? (
        <div className="composer-next">
          <span className="faint">Next question of “{followUp.label}”</span>
          <button className="chip" onClick={followUp.use} disabled={busy}>
            {followUp.question}
          </button>
        </div>
      ) : null}
      <div className="composer-box">
        <textarea
          ref={input}
          className="composer-input"
          aria-label="Question"
          placeholder={down ? "The API is not reachable" : "Ask a question about the policy corpus"}
          rows={2}
          value={question}
          maxLength={2000}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              run();
            }
          }}
        />
        <div className="composer-bar">
          <button className="btn small ghost" onClick={() => setOptions((o) => !o)} aria-expanded={options} title="How this page reads the evidence after an answer">
            <SlidersHorizontal aria-hidden /> Evidence loading
          </button>
          <span className="faint small hint">Enter to ask · Shift+Enter for a new line</span>
          <button className="btn primary" onClick={run} disabled={!canRun}>
            {busy ? "Running" : "Ask"} <CornerDownLeft aria-hidden />
          </button>
        </div>
        {options ? (
          <div className="composer-options">
            <label>
              <input type="checkbox" checked={settings.auto} onChange={(e) => setSettings({ ...settings, auto: e.target.checked })} />
              Load evidence text after each answer
            </label>
            <label>
              Mode
              <select className="select" value={settings.mode} onChange={(e) => setSettings({ ...settings, mode: e.target.value as RetrievalMode })}>
                <option value="iterative">iterative</option>
                <option value="single">single</option>
              </select>
            </label>
            <label>
              Round budget
              <select className="select" value={settings.maxRounds} disabled={settings.mode === "single"} onChange={(e) => setSettings({ ...settings, maxRounds: Number(e.target.value) })}>
                {[1, 2, 3, 4, 5].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            <p className="faint small">
              POST /query returns chunk ids, not text. To show evidence text and retrieval rounds, this page sends the turn's query to the retrieval
              service again (POST /retrieve) with these settings. They do not change how the answer is produced.
            </p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
