// The workspace: one session as a conversation. Each turn is a question, its grounded answer and its sources;
// the composer continues the session. Everything else about a turn opens in the inspector.

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { QueryComposer } from "../components/QueryComposer";
import { TurnCard } from "../components/TurnCard";
import { ErrorState, Id, Note, Pending, StatusBadge } from "../components/ui";
import { EXAMPLES, type Example } from "../examples";
import { count, thread } from "../lib/model";
import { useStore } from "../state/store";

function Welcome({ onChoose }: { onChoose: (example: Example) => void }) {
  return (
    <div className="welcome">
      <h1>Ask the policy corpus</h1>
      <p>
        Every answer is grounded in retrieved policy text and cites it, or the system abstains. Open a citation to read the evidence and its source
        document; inspect the execution to see how retrieval and generation produced the answer.
      </p>
      <h2 className="eyebrow">Questions from the evaluation material</h2>
      <ul className="examples">
        {EXAMPLES.map((example) => (
          <li key={example.source}>
            <button className="example" onClick={() => onChoose(example)} title={example.source}>
              <span className="label">
                {example.label}
                {example.questions.length > 1 ? <span className="faint"> · {example.questions.length} turns</span> : null}
              </span>
              <span className="q">{example.questions[0]}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function WorkspaceView({ menu }: { menu: ReactNode }) {
  const { runs, sessionId, sessions, sessionTitles, health, loadSession, knownSessions } = useStore();
  const turns = thread(runs, sessionId);
  const entry = sessions[sessionId];
  const known = knownSessions.includes(sessionId);
  const [draft, setDraft] = useState<string | null>(null);
  const [queue, setQueue] = useState<{ example: Example; next: number; sessionId: string } | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const generation = health.generation.health;

  // A new turn, or another session: show the end of the conversation.
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns.length, sessionId]);

  const onDraftUsed = useCallback(() => setDraft(null), []);

  function choose(example: Example) {
    setDraft(example.questions[0]);
    setQueue(example.questions.length > 1 ? { example, next: 1, sessionId } : null);
  }

  const next = queue && queue.sessionId === sessionId && queue.next < queue.example.questions.length && turns.length > 0 ? queue : null;

  return (
    <div className="center">
      <header className="center-head">
        {menu}
        <div className="center-title">
          <strong>{sessionTitles[sessionId] ?? turns[0]?.question ?? "New session"}</strong>
          <span className="meta">
            <Id>{sessionId}</Id>
            {turns.length ? <span className="faint"> · {count(turns.length, "turn")}</span> : null}
          </span>
        </div>
        <div className="center-actions">
          {generation ? (
            generation.provider === "offline-stub" ? (
              <span title="The generation service runs its stub model (GENERATION_PROVIDER=offline): every answer is an abstention.">
                <StatusBadge tone="warn">Stub model: answers abstain</StatusBadge>
              </span>
            ) : (
              <span className="faint small mono" title="The model the generation service reports (GET /health)">
                {generation.model ?? generation.provider}
              </span>
            )
          ) : null}
          {known ? (
            <button className="icon-btn bare" onClick={() => void loadSession(sessionId)} title="Read the stored session again" aria-label="Read the stored session again">
              <RefreshCw aria-hidden />
            </button>
          ) : null}
        </div>
      </header>

      <div className="thread" ref={scroller}>
        <div className="thread-inner">
          {health.api.state === "down" ? (
            <Note tone="err">
              <strong>The API is not reachable.</strong> Queries cannot run until <span className="mono">{__SERVICE_TARGETS__.api}</span> answers. {health.api.error}
            </Note>
          ) : null}
          {entry?.error ? <ErrorState title="The stored session could not be read">{entry.error}</ErrorState> : null}
          {entry?.notFound && !turns.length && known ? (
            <Note tone="warn">The backend has no session with this id: it was never answered, or the session store no longer holds it.</Note>
          ) : null}
          {turns.length ? (
            turns.map((run) => <TurnCard key={run.id} run={run} />)
          ) : entry?.loading ? (
            <Pending>
              Reading <span className="mono">GET /sessions/{sessionId}</span>
            </Pending>
          ) : (
            <Welcome onChoose={choose} />
          )}
        </div>
      </div>

      <div className="dock">
        <div className="dock-inner">
          <QueryComposer
            draft={draft}
            onDraftUsed={onDraftUsed}
            followUp={
              next
                ? {
                    label: next.example.label,
                    question: next.example.questions[next.next],
                    use: () => {
                      setDraft(next.example.questions[next.next]);
                      setQueue({ ...next, next: next.next + 1 });
                    },
                  }
                : null
            }
          />
        </div>
      </div>
    </div>
  );
}
