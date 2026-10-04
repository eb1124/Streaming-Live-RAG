// The inspector's frame: what kind of thing is open, its title, and the way back.

import { ArrowLeft, X } from "lucide-react";
import type { ReactNode } from "react";
import { useStore } from "../../state/store";

export function Frame({ kind, badge, title, subtitle, children }: { kind: string; badge?: ReactNode; title: ReactNode; subtitle?: ReactNode; children: ReactNode }) {
  const { canGoBack, backPanel, closePanel } = useStore();
  return (
    <>
      <header className="inspector-head">
        <div className="inspector-bar">
          {canGoBack ? (
            <button className="icon-btn bare" onClick={backPanel} aria-label="Back" title="Back">
              <ArrowLeft aria-hidden />
            </button>
          ) : null}
          <span className="eyebrow">{kind}</span>
          {badge}
          <button className="icon-btn bare close" onClick={closePanel} aria-label="Close the inspector" title="Close">
            <X aria-hidden />
          </button>
        </div>
        <h2>{title}</h2>
        {subtitle ? <p className="muted">{subtitle}</p> : null}
      </header>
      <div className="inspector-body">{children}</div>
    </>
  );
}
