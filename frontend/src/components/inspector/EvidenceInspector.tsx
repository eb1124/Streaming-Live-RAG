// One chunk of evidence, opened: its text, how it was retrieved, what the answer made of it, and its document.

import { entryTitle, entryWhere } from "../SourceList";
import { evidenceEntry, type Run } from "../../lib/model";
import { useStore } from "../../state/store";
import { StatusBadge } from "../ui";
import { Frame } from "./Frame";
import { ClaimsSupported, EvidenceText, InspectorSection, RetrievalFacts, SourceFacts } from "./parts";

export function EvidenceInspector({ run, chunkId }: { run: Run; chunkId: string }) {
  const { openPanel } = useStore();
  const entry = evidenceEntry(run, chunkId);
  return (
    <Frame
      kind="Evidence"
      badge={entry.label ? <span className="mono faint">{entry.label}</span> : undefined}
      title={entryTitle(entry) ?? chunkId}
      subtitle={entryWhere(entry) || undefined}
    >
      <InspectorSection
        title="Evidence"
        aside={entry.shownToModel ? <StatusBadge plain>shown to the model</StatusBadge> : <StatusBadge plain>retrieved, not shown</StatusBadge>}
      >
        <EvidenceText run={run} entry={entry} />
      </InspectorSection>
      <InspectorSection title="Retrieval">
        <RetrievalFacts run={run} entry={entry} />
      </InspectorSection>
      <InspectorSection
        title="Supports"
        aside={entry.citations.map((c) => (
          <button key={c.number} className="marker" onClick={() => openPanel({ kind: "source", runId: run.id, chunkId, citation: c.number }, true)} aria-label={`Open citation ${c.number}`}>
            {c.number}
          </button>
        ))}
      >
        {entry.citations.length ? <ClaimsSupported run={run} chunkId={chunkId} /> : <p className="muted small">The answer does not cite this chunk.</p>}
      </InspectorSection>
      <InspectorSection title="Source">
        <SourceFacts entry={entry} />
      </InspectorSection>
    </Frame>
  );
}
