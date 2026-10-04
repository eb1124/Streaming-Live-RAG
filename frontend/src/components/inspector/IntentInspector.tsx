// One intent of a multi-intent question, from the stored turn: its sub-query, what its retrieval found, the
// evidence shown for it and the claims that answer it.

import { EvidenceList } from "../SourceList";
import { RetrievalBlock } from "../RetrievalInspector";
import { claimsOf, evidenceShown, inspectionTargets, score, type Run } from "../../lib/model";
import { MetricRow, Metrics, Note, Raw, StatusBadge } from "../ui";
import { Frame } from "./Frame";
import { ClaimCitations, InspectorSection } from "./parts";

export function IntentInspector({ run, index }: { run: Run; index: number }) {
  const answer = run.turn?.answer;
  const intent = answer?.intents?.find((i) => i.index === index);
  if (!intent) {
    return (
      <Frame kind="Intent" title={`Intent ${index + 1}`}>
        <Note tone="warn">The stored turn has no record of this intent{run.turnError ? `: ${run.turnError}` : "."}</Note>
      </Frame>
    );
  }
  const target = inspectionTargets(run).find((t) => t.key === `intent-${index}`);
  const evidence = evidenceShown(run).filter((e) => e.intents.some((i) => i.intent === index));
  const claims = claimsOf(run).filter(({ claim }) => claim.intents?.includes(index));
  const tone = intent.status === "answered" ? "ok" : intent.status ? "warn" : "neutral";
  return (
    <Frame
      kind="Intent"
      badge={<span className="mono faint">{index + 1} of {answer?.intents.length}</span>}
      title={intent.text}
      subtitle={intent.organizations.join(", ") || undefined}
    >
      <InspectorSection title="Intent" aside={intent.status ? <StatusBadge tone={tone}>{intent.status.replace(/_/g, " ")}</StatusBadge> : undefined}>
        <Metrics>
          <MetricRow label="Sub-query">{intent.sub_query}</MetricRow>
          <MetricRow label="Organizations">{intent.organizations.join(", ")}</MetricRow>
          {intent.outside_corpus?.length ? <MetricRow label="Outside the corpus">{intent.outside_corpus.join(", ")}</MetricRow> : null}
          {intent.temporal_intent ? <MetricRow label="Temporal intent">{intent.temporal_intent.replace(/_/g, " ")}</MetricRow> : null}
          {intent.retrieved ? <MetricRow label="Retrieved">{intent.retrieved.length} chunks</MetricRow> : null}
          {intent.best_rerank !== undefined ? (
            <MetricRow label="Best cross-encoder score" mono>
              {score(intent.best_rerank)}
            </MetricRow>
          ) : null}
          {intent.admissible !== undefined ? <MetricRow label="Admissible">{intent.admissible ? "yes" : "no"}</MetricRow> : null}
          {intent.covered_by_question_context !== undefined ? (
            <MetricRow label="Covered by the whole-question context">{intent.covered_by_question_context ? "yes" : "no"}</MetricRow>
          ) : null}
        </Metrics>
        <Raw label="Stored intent record" value={intent} />
      </InspectorSection>

      {target ? (
        <InspectorSection title="Retrieval">
          <RetrievalBlock run={run} target={target} />
        </InspectorSection>
      ) : null}

      <InspectorSection title="Evidence" aside={`${evidence.length} shown for this intent`}>
        {evidence.length ? <EvidenceList run={run} entries={evidence} push /> : <p className="muted small">No chunk shown to the model is recorded for this intent.</p>}
      </InspectorSection>

      <InspectorSection title="Claims">
        {claims.length ? (
          <ol className="claims">
            {claims.map(({ number, claim }) => (
              <li key={number}>
                <div className="claim-head">
                  <span className="claim-n">Claim {number}</span>
                  <ClaimCitations numbers={claim.citations ?? []} />
                </div>
                <p>{claim.text}</p>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted small">No verified claim answers this intent.</p>
        )}
      </InspectorSection>
    </Frame>
  );
}
