// Pure view-model helpers: they only rearrange what the backend returned. No value is computed that the backend did
// not send, apart from counts of lists it sent.

import type { Exchange } from "../api/client";
import type {
  Citation,
  Claim,
  EvidenceItem,
  IntentRecord,
  QueryResponse,
  ResolutionView,
  RetrievalRequest,
  RetrievalResponse,
  TurnDetail,
} from "../api/types";

export interface Inspection {
  key: string; // "question" or "intent-<n>"
  label: string;
  request: RetrievalRequest;
  pending: boolean;
  result?: RetrievalResponse;
  exchange?: Exchange;
  error?: string;
}

export interface Run {
  id: string;
  question: string;
  sessionId: string;
  requestId?: string;
  startedAt?: string;
  pending: boolean;
  /** A turn read from the stored session (GET /sessions/{id}) that this page did not ask: it has no POST /query
   *  exchange, so no request id, job id, timing or trace context. */
  stored?: boolean;
  response?: QueryResponse;
  exchange?: Exchange;
  error?: string;
  /** The stored turn (GET /sessions/{id}), fetched after the answer: undefined until loaded. */
  turn?: TurnDetail;
  turnExchange?: Exchange;
  turnError?: string;
  inspections: Record<string, Inspection>;
}

/** The orchestrator asks the retrieval service for this many chunks (generation.pipeline.RETRIEVE_K). */
export const ORCHESTRATOR_K = 10;

// ---- the answer text and its [n] markers

export type Segment = { kind: "text"; text: string } | { kind: "marker"; number: number };

/** Split answer text at its citation markers, "[1]" or "[1, 2]", keeping only numbers that are citations. */
export function segments(text: string, citations: Citation[]): Segment[] {
  const known = new Set(citations.map((c) => c.number));
  const out: Segment[] = [];
  let last = 0;
  for (const match of text.matchAll(/\[(\d+(?:\s*,\s*\d+)*)\]/g)) {
    const numbers = match[1].split(",").map((n) => Number(n.trim()));
    if (!numbers.every((n) => known.has(n))) continue;
    if (match.index! > last) out.push({ kind: "text", text: text.slice(last, match.index) });
    for (const number of numbers) out.push({ kind: "marker", number });
    last = match.index! + match[0].length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}

// ---- one turn, whichever response it was read from

export type RunState = "pending" | "error" | "failed" | "answered" | "abstained";

/** What a completed turn says. */
export interface Outcome {
  answered: boolean;
  status: string | null;
  text: string | null;
  citations: Citation[];
  evidence: string[]; // chunk ids shown to the model
  abstentionReason: string | null;
  resolution: ResolutionView | null;
  strategy: string | null;
  turnIndex: number | null;
}

/** The chunk ids a stored turn's model answers were shown, in order, each once: what QueryResponse.evidence holds. */
export function storedEvidence(turn: TurnDetail): string[] {
  const ids = new Set<string>();
  for (const part of turn.answer?.phase1 ?? []) for (const s of part.sources_considered ?? []) ids.add(s.chunk_id);
  return [...ids];
}

/** The outcome of a run: from POST /query, or, for a turn read from the stored session, from the stored turn by the
 *  mapping the API itself uses (services/contracts.py, QueryResponse.of_turn). Null while pending or when it failed. */
export function outcomeOf(run: Run): Outcome | null {
  const r = run.response;
  if (r) {
    if (r.status !== "completed") return null;
    return {
      answered: r.answer_status === "answered",
      status: r.answer_status,
      text: r.text,
      citations: r.citations ?? [],
      evidence: r.evidence ?? [],
      abstentionReason: r.abstention_reason,
      resolution: r.resolution,
      strategy: r.strategy,
      turnIndex: r.turn_index,
    };
  }
  const turn = run.stored ? run.turn : undefined;
  if (!turn) return null;
  const answer = turn.answer;
  return {
    answered: turn.status === "answered",
    status: turn.status,
    text: turn.text,
    citations: answer?.citations ?? [],
    evidence: storedEvidence(turn),
    abstentionReason: answer ? answer.abstention_reason : "unresolved_reference",
    resolution: turn.resolution,
    strategy: answer?.strategy ?? null,
    turnIndex: turn.index,
  };
}

export function runState(run: Run): RunState {
  if (run.pending) return "pending";
  if (run.response?.status === "failed") return "failed";
  const outcome = outcomeOf(run);
  if (!outcome) return "error";
  return outcome.answered ? "answered" : "abstained";
}

export function turnIndexOf(run: Run): number | null {
  return run.response?.turn_index ?? run.turn?.index ?? null;
}

/** The runs of one session in conversation order: by the backend's turn index; a run without one (running, failed)
 *  stays where it was asked. */
export function thread(runs: Run[], sessionId: string): Run[] {
  let last = -1;
  return runs
    .filter((run) => run.sessionId === sessionId)
    .map((run, at) => {
      const index = turnIndexOf(run);
      return { run, at, key: index != null ? (last = index) : last + 0.5 };
    })
    .sort((a, b) => a.key - b.key || a.at - b.at)
    .map((x) => x.run);
}

// ---- evidence

export interface EvidenceEntry {
  chunkId: string;
  shownToModel: boolean;
  label: string | null; // the context label the model saw (S1, S2, ...), from the stored turn
  retrievalRank: number | null; // its rank in the retrieval the model's context was built from, from the stored turn
  citations: Citation[]; // citations of the answer that point at this chunk
  intents: { intent: number; rank: number; score: number | null }[]; // per intent: rank and cross-encoder score
  multi: boolean; // the question was split into several intents
  item: EvidenceItem | null; // the chunk with its text, when a retrieval inspection returned it
  itemFrom: string | null; // which inspection
  firstRound: number | null; // the round of that inspection's trace that first retrieved it
  promoted: boolean; // moved into the context by that trace to close an organization gap
}

/** The inspections of a run: the whole question first, then the intents in order. */
export function inspectionsOf(run: Run): Inspection[] {
  const first = (i: Inspection) => (i.key === "question" ? 0 : 1);
  return Object.values(run.inspections).sort((a, b) => first(a) - first(b) || a.key.localeCompare(b.key, undefined, { numeric: true }));
}

function locate(run: Run, chunkId: string) {
  for (const inspection of inspectionsOf(run)) {
    const item = inspection.result?.evidence.find((e) => e.chunk.chunk_id === chunkId);
    if (!item) continue;
    const rounds = inspection.result?.trace?.rounds ?? [];
    const first = rounds.find((r) => r.new.includes(chunkId));
    return {
      item,
      itemFrom: inspection.label,
      firstRound: first ? first.iteration : null,
      promoted: rounds.some((r) => r.promoted.includes(chunkId)),
    };
  }
  return { item: null, itemFrom: null, firstRound: null, promoted: false };
}

/** The evidence ids and citations of a run: POST /query's when the page asked it, the stored turn's otherwise. */
function summaryOf(run: Run): { evidence: string[]; citations: Citation[] } | null {
  if (run.response) return { evidence: run.response.evidence ?? [], citations: run.response.citations ?? [] };
  if (run.stored && run.turn) return { evidence: storedEvidence(run.turn), citations: run.turn.answer?.citations ?? [] };
  return null;
}

/** Everything known about one chunk of a run. */
export function evidenceEntry(run: Run, chunkId: string): EvidenceEntry {
  const summary = summaryOf(run);
  const answer = run.turn?.answer ?? null;
  let considered: { label: string; retrieval_rank?: number } | undefined;
  for (const part of answer?.phase1 ?? []) {
    considered = (part.sources_considered ?? []).find((s) => s.chunk_id === chunkId);
    if (considered) break;
  }
  return {
    chunkId,
    shownToModel: summary?.evidence.includes(chunkId) ?? false,
    label: considered?.label ?? null,
    retrievalRank: considered?.retrieval_rank ?? null,
    citations: (summary?.citations ?? []).filter((c) => c.chunk_id === chunkId),
    intents: answer?.evidence_intents?.[chunkId] ?? [],
    multi: answer?.decomposition?.multi ?? false,
    ...locate(run, chunkId),
  };
}

/** The chunks the model was shown for this answer, in the backend's order, joined with everything known about each. */
export function evidenceShown(run: Run): EvidenceEntry[] {
  return (summaryOf(run)?.evidence ?? []).map((chunkId) => evidenceEntry(run, chunkId));
}

/** Chunks an inspection retrieved that the model was not shown. */
export function evidenceRetrievedOnly(run: Run): EvidenceEntry[] {
  const shown = new Set(summaryOf(run)?.evidence ?? []);
  const seen = new Set<string>();
  const out: EvidenceEntry[] = [];
  for (const inspection of inspectionsOf(run)) {
    for (const e of inspection.result?.evidence ?? []) {
      const id = e.chunk.chunk_id;
      if (shown.has(id) || seen.has(id)) continue;
      seen.add(id);
      out.push(evidenceEntry(run, id));
    }
  }
  return out;
}

// ---- claims

export interface ClaimRef {
  number: number; // its position in the stored answer's claims, from 1
  claim: Claim;
}

export function claimsOf(run: Run): ClaimRef[] {
  return (run.turn?.answer?.claims ?? []).map((claim, i) => ({ number: i + 1, claim }));
}

/** The verified claims that cite this chunk: a claim's citation numbers, through the answer's citations. */
export function claimsFor(run: Run, chunkId: string): ClaimRef[] {
  const numbers = new Set((summaryOf(run)?.citations ?? []).filter((c) => c.chunk_id === chunkId).map((c) => c.number));
  return claimsOf(run).filter(({ claim }) => (claim.citations ?? []).some((n) => numbers.has(n)));
}

/** A claim as the backend writes it into the answer text: its text, closed with a full stop, after the label of
 *  the document version it is about when the answer was split by version. Its citation numbers are not part of it. */
export function claimSentence(claim: Claim): string {
  const text = /[.?!]$/.test(claim.text) ? claim.text : `${claim.text}.`;
  return claim.version_label ? `${claim.version_label}: ${text}` : text;
}

export interface IntentAnswer {
  intent: IntentRecord;
  claims: ClaimRef[]; // the verified claims the backend attributes to this intent; none: it has no grounded answer
  gaps: string[]; // what the backend says the sources do not answer for this intent
}

export interface AnswerByIntent {
  intents: IntentAnswer[];
  unattributed: ClaimRef[]; // claims the backend attributes to no intent
  gaps: string[]; // "not in the sources" statements that are about the answer as a whole
}

/** The answer of a question the backend split into intents, intent by intent: each stored claim under the intents
 *  it answers (claim.intents), and the stored gaps under the intent they name. Null when the question was not
 *  split or the stored turn is not loaded. Nothing is reworded and no claim is dropped. */
export function answerByIntent(run: Run): AnswerByIntent | null {
  const answer = run.turn?.answer;
  const records = answer?.intents ?? [];
  if (!answer?.decomposition?.multi || records.length < 2) return null;
  const claims = claimsOf(run);
  let gaps = answer.not_in_sources ?? [];
  const intents = records.map((intent) => {
    // The per-intent strategy labels a gap "Part <n> (<intent text>): ..." (adaptive/multi/controller.py).
    const label = `Part ${intent.index + 1} (${intent.text}): `;
    const own = gaps.filter((g) => g.startsWith(label));
    gaps = gaps.filter((g) => !g.startsWith(label));
    return { intent, claims: claims.filter(({ claim }) => claim.intents?.includes(intent.index)), gaps: own.map((g) => g.slice(label.length)) };
  });
  const known = new Set(records.map((i) => i.index));
  return { intents, unattributed: claims.filter(({ claim }) => !(claim.intents ?? []).some((k) => known.has(k))), gaps };
}

export interface OrganizationAnswer {
  organization: string;
  claims: ClaimRef[]; // the verified claims that cite a source of this organization; none: no grounded answer for it
}

export interface AnswerByOrganization {
  organizations: OrganizationAnswer[];
  other: ClaimRef[]; // claims whose citations carry no organization
  gaps: string[]; // the answer's "not in the sources" statements
}

/** The answer of a question the backend kept as ONE intent although it is about several organizations
 *  (resolution.organizations): its stored claims grouped by the organization of the sources each one cites
 *  (Citation.organization). An organization the question names that no claim cites has no grounded answer. This is
 *  a grouping of the stored claims, not the backend's intents: null when the backend split the question itself, when
 *  fewer than two organizations are named, or when the turn was not answered. */
export function answerByOrganization(run: Run): AnswerByOrganization | null {
  const turn = run.turn;
  const answer = turn?.answer;
  if (!turn || !answer || turn.status !== "answered" || answerByIntent(run)) return null;
  const named = turn.resolution?.organizations ?? [];
  if (named.length < 2) return null;
  const organizationOf = new Map((answer.citations ?? []).map((c) => [c.number, c.organization]));
  const claims = claimsOf(run).map((ref) => ({
    ref,
    organizations: new Set((ref.claim.citations ?? []).map((n) => organizationOf.get(n)).filter((o): o is string => Boolean(o))),
  }));
  const cited = claims.flatMap((c) => [...c.organizations]);
  const all = [...new Set([...named, ...cited])];
  return {
    organizations: all.map((organization) => ({ organization, claims: claims.filter((c) => c.organizations.has(organization)).map((c) => c.ref) })),
    other: claims.filter((c) => c.organizations.size === 0).map((c) => c.ref),
    gaps: answer.not_in_sources ?? [],
  };
}

export type TextPart = { text: string; quoted: boolean };

/** Evidence text split where a quote occurs in it verbatim (whitespace aside). A quote that does not occur marks
 *  nothing: no text is marked that the backend did not quote. */
export function markQuotes(text: string, quotes: string[]): TextPart[] {
  const ranges: [number, number][] = [];
  for (const quote of quotes) {
    const tokens = quote.trim().split(/\s+/).filter(Boolean);
    if (!tokens.length) continue;
    const pattern = new RegExp(tokens.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+"), "g");
    for (const match of text.matchAll(pattern)) ranges.push([match.index!, match.index! + match[0].length]);
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const out: TextPart[] = [];
  let at = 0;
  for (const [start, end] of ranges) {
    if (end <= at) continue;
    const from = Math.max(start, at);
    if (from > at) out.push({ text: text.slice(at, from), quoted: false });
    out.push({ text: text.slice(from, end), quoted: true });
    at = end;
  }
  if (at < text.length) out.push({ text: text.slice(at), quoted: false });
  return out;
}

// ---- retrieval inspections: the queries of a run that POST /retrieve can be asked again

export interface InspectionTarget {
  key: string; // "question" | "intent-<index>"
  label: string;
  query: string;
}

/** The whole (resolved) question, then each intent's sub-query when the question was split. */
export function inspectionTargets(run: Run): InspectionTarget[] {
  const out: InspectionTarget[] = [];
  const resolution = outcomeOf(run)?.resolution ?? null;
  if (resolution && resolution.kind !== "unresolved" && resolution.query) {
    out.push({ key: "question", label: "Whole question", query: resolution.query });
  }
  const answer = run.turn?.answer;
  if (answer?.decomposition?.multi) {
    for (const intent of answer.intents ?? []) {
      out.push({ key: `intent-${intent.index}`, label: `Intent ${intent.index + 1}`, query: intent.sub_query });
    }
  }
  return out;
}

/** The intents whose stored record lists one of these chunks as retrieved. */
export function targetsFor(run: Run, chunkIds: string[]): InspectionTarget[] {
  const wanted = new Set(chunkIds);
  const intents = run.turn?.answer?.intents ?? [];
  return inspectionTargets(run).filter((target) => {
    const intent = intents.find((i) => `intent-${i.index}` === target.key);
    return Boolean(intent?.retrieved?.some((id) => wanted.has(id)));
  });
}

// ---- formatting

export function ms(value: number | null | undefined): string {
  if (value == null) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`;
}

export function seconds(value: number): string {
  return value >= 1 ? `${value.toFixed(2)} s` : `${(value * 1000).toFixed(1)} ms`;
}

export function score(value: number | null | undefined): string {
  return value == null ? "—" : value.toFixed(3);
}

export function pagesOf(pageStart: number, pageEnd: number): string {
  return pageStart === pageEnd ? `p. ${pageStart}` : `pp. ${pageStart}–${pageEnd}`;
}

export function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour12: false });
}

export function count(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

/** Human words for the backend's codes; an unknown code is shown as it is. */
const WORDS: Record<string, string> = {
  model_insufficient_evidence: "The model found the retrieved evidence insufficient",
  low_relevance: "No retrieved source was relevant enough to show the model",
  no_evidence: "Nothing was retrieved",
  ungrounded_output: "The model's output could not be verified against the sources",
  provider_error: "The model provider failed",
  unresolved_reference: "The question refers to earlier context that could not be resolved",
  self_contained: "Self-contained",
  follow_up: "Follow-up",
  unresolved: "Unresolved",
  delegate: "Delegated to the single-question path",
  fused: "Evidence of the intents fused into one answer",
  per_intent: "Each intent answered separately",
  no_supported_intent: "No intent could be supported",
  single: "One answer",
  split_by_version: "Split by document version",
};

/** The heading of an abstention, by the backend's abstention_reason code. */
export function abstentionTitle(reason: string | null | undefined): string {
  if (reason === "unresolved_reference") return "Unresolved reference";
  if (reason === "provider_error") return "Model provider error";
  if (reason === "ungrounded_output") return "Answer rejected by the verifier";
  return "Insufficient evidence";
}

export function words(code: string | null | undefined): string {
  if (!code) return "—";
  return WORDS[code] ?? code.replace(/_/g, " ");
}
