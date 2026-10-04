// Building blocks of the inspector's source and evidence panels. Each shows one kind of fact about a chunk and
// names where it comes from; a fact the backend did not return is said to be unavailable.

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import type { Citation } from "../../api/types";
import { claimsFor, inspectionTargets, markQuotes, pagesOf, score, targetsFor, type EvidenceEntry, type Run } from "../../lib/model";
import { useStore } from "../../state/store";
import { Disclosure, ErrorState, MetricRow, Metrics, Pending } from "../ui";

export function InspectorSection({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="isec">
      <h3>
        {title}
        {aside ? <span className="aside">{aside}</span> : null}
      </h3>
      {children}
    </section>
  );
}

/** The chunk's text as the retrieval service returned it, with the verifier's quotes marked where they occur in
 *  it verbatim. Without a retrieval that returned the chunk there is no text to show. */
export function EvidenceText({ run, entry }: { run: Run; entry: EvidenceEntry }) {
  const { loadEvidence, inspect, health } = useStore();
  const chunk = entry.item?.chunk;
  const box = useRef<HTMLQuoteElement>(null);
  const loaded = Boolean(chunk);
  const [full, setFull] = useState(false);
  const [long, setLong] = useState(false);

  // Another chunk starts collapsed again.
  useEffect(() => setFull(false), [entry.chunkId]);

  // A long chunk is shown as an excerpt until asked for in full. The excerpt is a window on the same text, placed
  // on the passage the verifier checked when there is one; nothing is cut out of the text itself.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    if (full) return;
    setLong(el.scrollHeight > el.clientHeight + 4);
    const mark = el.querySelector("mark");
    el.scrollTop = mark ? Math.max(0, mark.offsetTop - el.clientHeight / 3) : 0;
  }, [entry.chunkId, loaded, full]);

  if (chunk) {
    const quotes = claimsFor(run, entry.chunkId).flatMap(({ claim }) => claim.quotes ?? []);
    const parts = markQuotes(chunk.text, quotes);
    const marked = parts.some((p) => p.quoted);
    return (
      <>
        <blockquote className={`evidence-text ${full ? "full" : "excerpt"}`} ref={box}>
          {parts.map((part, i) => (part.quoted ? <mark key={i}>{part.text}</mark> : <span key={i}>{part.text}</span>))}
        </blockquote>
        {long || full ? (
          <button className="link" onClick={() => setFull((f) => !f)} aria-expanded={full}>
            {full ? "Show the excerpt" : `Show full evidence (${chunk.token_count} tokens)`}
          </button>
        ) : null}
        <p className="faint small">
          Text returned by the retrieval service for “{entry.itemFrom}”.
          {marked ? " Marked: the passages the verifier checked the claims against." : ""}
          {long && !full ? (marked ? " The excerpt is placed on the marked passage." : " The excerpt is the start of the chunk.") : ""}
        </p>
      </>
    );
  }
  const pending = Object.values(run.inspections).some((i) => i.pending);
  if (pending) return <Pending>Reading the evidence from the retrieval service</Pending>;
  const failed = Object.values(run.inspections).find((i) => i.error);
  const targets = inspectionTargets(run);
  const untried = [...targets.filter((t) => t.key === "question"), ...targetsFor(run, [entry.chunkId])].filter((t) => !run.inspections[t.key]?.result);
  return (
    <div className="unavailable">
      <p>
        <strong>The evidence text is not loaded.</strong> The query response carries this chunk's id only, and the backend has no lookup by chunk
        id: the text arrives when the retrieval service returns the chunk for one of this turn's queries.
      </p>
      {failed ? <ErrorState title="The retrieval request failed">{failed.error}</ErrorState> : null}
      {untried.length ? (
        <button className="btn small" disabled={health.retrieval.state === "down"} onClick={() => void loadEvidence(run.id)}>
          Load from the retrieval service
        </button>
      ) : targets.length ? (
        <>
          <p className="faint small">
            The retrieval was asked again for {targets.filter((t) => run.inspections[t.key]?.result).map((t) => t.label.toLowerCase()).join(", ")} and did not
            return this chunk. The orchestrator's own retrieval may have run in another mode.
          </p>
          <button
            className="btn small"
            disabled={health.retrieval.state === "down"}
            onClick={() => void inspect(run.id, targets[0].key, targets[0].label, targets[0].query, { mode: run.inspections[targets[0].key]?.request.mode === "single" ? "iterative" : "single", maxRounds: 3 })}
          >
            Try the other retrieval mode
          </button>
        </>
      ) : (
        <p className="faint small">No retrieval ran for this turn.</p>
      )}
    </div>
  );
}

/** The citation numbers of a claim, as badges of their own: "Claim 3" then "[1] [2]", never run together. */
export function ClaimCitations({ numbers }: { numbers: number[] }) {
  if (!numbers.length) return null;
  return (
    <span className="claim-cites" aria-label={`cites ${numbers.join(", ")}`}>
      {numbers.map((n) => (
        <span key={n} className="cite">
          [{n}]
        </span>
      ))}
    </span>
  );
}

/** The verified claims of the stored answer that cite this chunk, with the passages the verifier checked. */
export function ClaimsSupported({ run, chunkId }: { run: Run; chunkId: string }) {
  const claims = claimsFor(run, chunkId);
  const answer = run.turn?.answer;
  if (!claims.length) {
    return (
      <p className="muted small">
        {run.turnError
          ? `The claims are in the stored turn, which could not be read: ${run.turnError}`
          : !run.turn
            ? "Reading the stored turn."
            : !answer || !answer.claims?.length
              ? "The stored answer has no verified claims."
              : "No claim of the answer cites this chunk."}
      </p>
    );
  }
  const multi = answer?.decomposition?.multi ?? false;
  return (
    <ol className="claims">
      {claims.map(({ number, claim }) => (
        <li key={number}>
          <div className="claim-head">
            <span className="claim-n">Claim {number}</span>
            <ClaimCitations numbers={claim.citations ?? []} />
            {multi && claim.intents?.length ? <span className="faint small">intent {claim.intents.map((k) => k + 1).join(", ")}</span> : null}
          </div>
          <p>{claim.text}</p>
          {(claim.quotes ?? []).map((quote, i) => (
            <q key={i}>{quote}</q>
          ))}
        </li>
      ))}
    </ol>
  );
}

/** The document a chunk comes from. With the chunk loaded, every field is its own metadata from the retrieval
 *  service; without it, only what the citation carries. */
export function SourceFacts({ entry, citation }: { entry: EvidenceEntry; citation?: Citation | null }) {
  const chunk = entry.item?.chunk;
  const cited = citation ?? entry.citations[0] ?? null;
  if (!chunk) {
    return (
      <>
        <Metrics>
          <MetricRow label="Document">{cited?.document}</MetricRow>
          <MetricRow label="Organization">{cited?.organization}</MetricRow>
          <MetricRow label="Effective date">{cited?.effective_date}</MetricRow>
          <MetricRow label="Location">{[cited?.pages || null, cited?.section, cited?.clause ? `clause ${cited.clause}` : null].filter(Boolean).join(" · ")}</MetricRow>
          <MetricRow label="Document id" mono>
            {cited?.doc_id}
          </MetricRow>
          <MetricRow label="Chunk id" mono>
            {entry.chunkId}
          </MetricRow>
        </Metrics>
        <p className="faint small">Version, source URL and content hash are part of the chunk, which is not loaded.</p>
      </>
    );
  }
  return (
    <>
      <Metrics>
        <MetricRow label="Document">{chunk.title}</MetricRow>
        <MetricRow label="Organization">{chunk.organization}</MetricRow>
        <MetricRow label="Version">{chunk.version}</MetricRow>
        <MetricRow label="Effective date">{chunk.effective_date}</MetricRow>
        <MetricRow label="Location">
          {[pagesOf(chunk.page_start, chunk.page_end), chunk.section_path.join(" › ") || null, chunk.clause_id ? `clause ${chunk.clause_id}` : null].filter(Boolean).join(" · ")}
        </MetricRow>
        <MetricRow label="Source URL">
          {chunk.source_url ? (
            <a href={chunk.source_url} target="_blank" rel="noreferrer">
              {chunk.source_url}
            </a>
          ) : null}
        </MetricRow>
        <MetricRow label="Content hash" mono>
          {chunk.content_hash}
        </MetricRow>
      </Metrics>
      <Disclosure summary="More document metadata">
        <Metrics>
          <MetricRow label="Current version">{chunk.is_current == null ? null : chunk.is_current ? "yes" : "no"}</MetricRow>
          <MetricRow label="Superseded date">{chunk.superseded_date}</MetricRow>
          <MetricRow label="Type">{[chunk.document_type, chunk.domain].filter(Boolean).join(" · ")}</MetricRow>
          <MetricRow label="Series" mono>
            {chunk.series_id}
          </MetricRow>
          <MetricRow label="Document id" mono>
            {chunk.doc_id}
          </MetricRow>
          <MetricRow label="Chunk id" mono>
            {chunk.chunk_id}
          </MetricRow>
          <MetricRow label="Tokens">{chunk.token_count}</MetricRow>
        </Metrics>
      </Disclosure>
      <p className="faint small">
        The backend serves no document file or page image. The source URL is where the document was captured from; the text above is the indexed
        chunk.
      </p>
    </>
  );
}

/** How the chunk was retrieved: what the orchestrator stored for this turn first, then what this page's own
 *  retrieval request returned. */
export function RetrievalFacts({ run, entry }: { run: Run; entry: EvidenceEntry }) {
  const { openPanel } = useStore();
  const hasTrace = Object.values(run.inspections).some((i) => i.result?.trace);
  return (
    <>
      <Metrics>
        <MetricRow label="Shown to the model">{entry.shownToModel ? (entry.label ? `yes, as ${entry.label}` : "yes") : "no"}</MetricRow>
        {entry.retrievalRank != null ? <MetricRow label="Rank in the model's context">{entry.retrievalRank}</MetricRow> : null}
        {entry.intents.map((i) => (
          <MetricRow key={i.intent} label={entry.multi ? `Intent ${i.intent + 1}` : "Orchestrator's retrieval"}>
            rank {i.rank} · cross-encoder score <span className="mono">{score(i.score)}</span>
          </MetricRow>
        ))}
        {entry.item ? (
          <MetricRow label={`This page's retrieval (${entry.itemFrom?.toLowerCase()})`}>
            rank {entry.item.rank}
            {entry.item.rerank_score != null ? (
              <>
                {" "}
                · rerank score <span className="mono">{score(entry.item.rerank_score)}</span>
              </>
            ) : null}
            {entry.item.retriever ? ` · ${entry.item.retriever}` : ""}
          </MetricRow>
        ) : null}
        <MetricRow label="Retrieval round">
          {entry.firstRound != null ? `first retrieved in round ${entry.firstRound}${entry.promoted ? " · promoted into the context" : ""}` : null}
        </MetricRow>
      </Metrics>
      {entry.firstRound == null ? (
        <p className="faint small">
          {hasTrace && entry.item
            ? "The loaded round trace does not list this chunk as new in any round."
            : "Rounds come from an iterative retrieval of this page; none that returned this chunk is loaded."}
        </p>
      ) : null}
      <button className="link" onClick={() => openPanel({ kind: "execution", runId: run.id }, true)}>
        Open the execution of this turn
      </button>
    </>
  );
}
