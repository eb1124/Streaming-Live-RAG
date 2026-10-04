// Small presentational building blocks shared by every view.

import { AlertTriangle, Info, OctagonAlert } from "lucide-react";
import type { ReactNode } from "react";

export type Tone = "ok" | "warn" | "err" | "neutral";

export function StatusBadge({ tone = "neutral", plain, children }: { tone?: Tone; plain?: boolean; children: ReactNode }) {
  return <span className={`badge ${tone === "neutral" ? "" : tone} ${plain ? "plain" : ""}`}>{children}</span>;
}

export function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="section">
      <h2>
        {title}
        {aside ? <span className="aside">{aside}</span> : null}
      </h2>
      {children}
    </section>
  );
}

/** One label/value row. A value the backend did not send is shown as unavailable, never filled in. */
export function MetricRow({ label, children, mono }: { label: string; children: ReactNode; mono?: boolean }) {
  const missing = children === null || children === undefined || children === "";
  return (
    <div className="kv">
      <dt>{label}</dt>
      <dd className={mono && !missing ? "mono" : undefined}>{missing ? <span className="faint">not available</span> : children}</dd>
    </div>
  );
}

export function Metrics({ children }: { children: ReactNode }) {
  return <dl className="kvs">{children}</dl>;
}

export function Id({ children }: { children: ReactNode }) {
  return <span className="id">{children}</span>;
}

export function Note({ tone = "info", children }: { tone?: "info" | "warn" | "err"; children: ReactNode }) {
  const Icon = tone === "err" ? OctagonAlert : tone === "warn" ? AlertTriangle : Info;
  return (
    <div className={`note ${tone === "info" ? "" : tone}`} role={tone === "err" ? "alert" : undefined}>
      <Icon aria-hidden />
      <div>{children}</div>
    </div>
  );
}

export function ErrorState({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <Note tone="err">
      <strong>{title}</strong>
      <div>{children}</div>
      {action ? <div style={{ marginTop: 8 }}>{action}</div> : null}
    </Note>
  );
}

export function Pending({ children }: { children: ReactNode }) {
  return (
    <div className="pending" role="status" aria-live="polite">
      <span className="spinner" aria-hidden />
      <span>{children}</span>
    </div>
  );
}

export function Disclosure({ summary, children, open }: { summary: ReactNode; children: ReactNode; open?: boolean }) {
  return (
    <details className="disclosure" open={open}>
      <summary>{summary}</summary>
      {children}
    </details>
  );
}

/** Raw backend data, collapsed: for verification, not for reading. */
export function Raw({ label = "Technical details", value }: { label?: string; value: unknown }) {
  return (
    <Disclosure summary={label}>
      <pre className="raw">{JSON.stringify(value, null, 2)}</pre>
    </Disclosure>
  );
}
