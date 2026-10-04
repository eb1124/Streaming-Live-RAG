// What an answer rests on: its citations (the sources), and the chunks the model was shown (the evidence). A row
// opens the inspector; nothing here is more than the backend's citation or chunk metadata.

import { ChevronRight } from "lucide-react";
import type { Citation } from "../api/types";
import { pagesOf, type EvidenceEntry, type Run } from "../lib/model";
import { useStore } from "../state/store";

/** Where in the document, from a citation: the backend formats `pages` itself ("p. 3", "pp. 3-4"). */
export function citationWhere(c: Citation, brief = false): string {
  // The backend sends the section as its whole path ("A > B > C"); a list row shows the innermost heading only.
  const section = brief ? (c.section?.split(" > ").pop() ?? null) : c.section;
  return [c.organization, c.effective_date ? `effective ${c.effective_date}` : null, c.pages || null, section, c.clause && !brief ? `clause ${c.clause}` : null]
    .filter(Boolean)
    .join(" · ");
}

export function SourceList({ run, citations }: { run: Run; citations: Citation[] }) {
  const { panel, openPanel } = useStore();
  const active = panel?.kind === "source" && panel.runId === run.id ? panel.citation : null;
  return (
    <ol className="sources" aria-label="Sources">
      {citations.map((c) => (
        <li key={c.number}>
          <button
            className={`source-row ${active === c.number ? "on" : ""}`}
            aria-pressed={active === c.number}
            onClick={() => openPanel({ kind: "source", runId: run.id, chunkId: c.chunk_id, citation: c.number })}
          >
            <span className="n">{c.number}</span>
            <span className="body">
              <span className="doc">{c.document ?? c.doc_id}</span>
              <span className="where">{citationWhere(c, true)}</span>
            </span>
            <ChevronRight aria-hidden />
          </button>
        </li>
      ))}
    </ol>
  );
}

/** The title and location of a chunk, from the retrieved chunk when its text was loaded, else from its citation. */
export function entryTitle(entry: EvidenceEntry): string | null {
  return entry.item?.chunk.title ?? entry.citations[0]?.document ?? null;
}

export function entryWhere(entry: EvidenceEntry): string {
  const chunk = entry.item?.chunk;
  if (chunk) {
    return [chunk.organization, pagesOf(chunk.page_start, chunk.page_end), chunk.section_path[chunk.section_path.length - 1], chunk.clause_id ? `clause ${chunk.clause_id}` : null]
      .filter(Boolean)
      .join(" · ");
  }
  const cited = entry.citations[0];
  return cited ? [cited.organization, cited.pages || null, cited.section].filter(Boolean).join(" · ") : "";
}

export function EvidenceList({ run, entries, push }: { run: Run; entries: EvidenceEntry[]; push?: boolean }) {
  const { panel, openPanel } = useStore();
  const active = panel && (panel.kind === "evidence" || panel.kind === "source") && panel.runId === run.id ? panel.chunkId : null;
  return (
    <ul className="evidence-list">
      {entries.map((entry) => {
        const title = entryTitle(entry);
        return (
          <li key={entry.chunkId}>
            <button className={`evidence-row ${active === entry.chunkId ? "on" : ""}`} onClick={() => openPanel({ kind: "evidence", runId: run.id, chunkId: entry.chunkId }, push)}>
              <span className="label mono">{entry.label ?? "·"}</span>
              <span className="body">
                <span className="doc">{title ?? <span className="mono">{entry.chunkId}</span>}</span>
                <span className="where">{entryWhere(entry) || (title ? "" : "source not loaded: the response carries the chunk id only")}</span>
              </span>
              <span className="tags">
                {entry.citations.map((c) => (
                  <span key={c.number} className="marker static">
                    {c.number}
                  </span>
                ))}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
