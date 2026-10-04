// A citation, opened: the claim it supports, the evidence text behind the claim, and the document the evidence
// comes from, in that order. Then how the evidence was retrieved.

import { citationWhere } from "../SourceList";
import { evidenceEntry, outcomeOf, type Run } from "../../lib/model";
import { Frame } from "./Frame";
import { ClaimsSupported, EvidenceText, InspectorSection, RetrievalFacts, SourceFacts } from "./parts";

export function SourceInspector({ run, chunkId, citation }: { run: Run; chunkId: string; citation: number }) {
  const entry = evidenceEntry(run, chunkId);
  const cited = outcomeOf(run)?.citations.find((c) => c.number === citation) ?? entry.citations[0] ?? null;
  const title = entry.item?.chunk.title ?? cited?.document ?? cited?.doc_id ?? chunkId;
  return (
    <Frame kind="Source" badge={<span className="marker static">{citation}</span>} title={title} subtitle={cited ? citationWhere(cited) : undefined}>
      <div className="chain">
        <div className="chain-step">
          <h3>Claim</h3>
          <p className="chain-note">What the answer says on the strength of this source.</p>
          <ClaimsSupported run={run} chunkId={chunkId} />
        </div>
        <div className="chain-step">
          <h3>Evidence{entry.label ? <span className="aside mono">{entry.label}</span> : null}</h3>
          <p className="chain-note">The retrieved passage the model was shown.</p>
          <EvidenceText run={run} entry={entry} />
        </div>
        <div className="chain-step">
          <h3>Source</h3>
          <p className="chain-note">The document the passage comes from.</p>
          <SourceFacts entry={entry} citation={cited} />
        </div>
      </div>
      <InspectorSection title="Retrieval">
        <RetrievalFacts run={run} entry={entry} />
      </InspectorSection>
    </Frame>
  );
}
