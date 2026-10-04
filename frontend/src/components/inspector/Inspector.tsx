// The right region: one contextual inspector. What it shows depends on what was opened in the conversation
// (a citation, a chunk of evidence, the execution, an intent, the telemetry); it never opens by itself.

import { useEffect } from "react";
import { useStore } from "../../state/store";
import { EvidenceInspector } from "./EvidenceInspector";
import { ExecutionInspector } from "./ExecutionInspector";
import { IntentInspector } from "./IntentInspector";
import { SourceInspector } from "./SourceInspector";
import { TelemetryInspector } from "./TelemetryInspector";

export function Inspector() {
  const { panel, runById, closePanel } = useStore();
  const run = panel ? runById(panel.runId) : null;

  useEffect(() => {
    if (!panel) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closePanel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [panel, closePanel]);

  if (!panel || !run) return null;
  return (
    <>
      <div className="scrim inspector-scrim" onClick={closePanel} aria-hidden />
      <aside className="inspector" aria-label="Inspector">
        {panel.kind === "source" ? <SourceInspector run={run} chunkId={panel.chunkId} citation={panel.citation} /> : null}
        {panel.kind === "evidence" ? <EvidenceInspector run={run} chunkId={panel.chunkId} /> : null}
        {panel.kind === "execution" ? <ExecutionInspector run={run} /> : null}
        {panel.kind === "intent" ? <IntentInspector run={run} index={panel.index} /> : null}
        {panel.kind === "telemetry" ? <TelemetryInspector run={run} /> : null}
      </aside>
    </>
  );
}
