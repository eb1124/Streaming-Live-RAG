// The backend's contracts as the frontend reads them. Sources: services/contracts.py (QueryRequest, QueryResponse,
// RetrievalRequest, RetrievalResponse), adaptive/session/state.py and adaptive/multi/controller.py (the stored
// session returned by GET /sessions/{id}), adaptive/streaming/state.py (RetrievalTrace). Nothing is added here that
// the backend does not send; fields this UI does not read are left out or typed loosely.

export type ServiceName = "api" | "retrieval" | "generation" | "mcp";

export interface Health {
  status: string;
  service: string;
  chunks?: number; // retrieval, mcp
  reranker?: boolean; // retrieval
  provider?: string | null; // generation
  model?: string | null; // generation
  tools?: string[]; // mcp
  retrieval?: string; // mcp: its search backend
  documents?: number; // mcp
  database?: string; // mcp: the database adapter's name ("postgresql" | "none")
}

// ---- POST /query

export interface QueryRequest {
  session_id: string;
  question: string;
  request_id?: string;
}

export interface Citation {
  number: number;
  label: string;
  chunk_id: string;
  doc_id: string;
  organization: string | null;
  document: string | null;
  effective_date: string | null;
  pages: string; // already formatted by the backend: "p. 3" or "pp. 3-4"
  section: string | null;
  clause: string | null;
}

export interface ResolutionView {
  kind: "self_contained" | "follow_up" | "unresolved" | string;
  reason: string;
  query: string;
  temporal: string;
  organizations: string[];
  anchor: number | null;
}

export interface QueryResponse {
  schema_version: number;
  request_id: string;
  job_id: string;
  session_id: string;
  status: "completed" | "failed";
  error: { type?: string; message?: string } | null;
  turn_index: number | null;
  answer_status: "answered" | "abstained" | string | null;
  text: string | null;
  citations: Citation[];
  abstention_reason: string | null;
  resolution: ResolutionView | null;
  strategy: string | null;
  answer_strategies: string[];
  evidence: string[];
  reused_citations: string[];
}

// ---- GET /sessions/{session_id}

export interface DecomposedIntent {
  index: number;
  text: string;
  sub_query: string;
  organizations: string[];
  carried?: string[];
  outside_corpus?: string[];
}

export interface IntentRecord extends DecomposedIntent {
  temporal_intent?: string;
  selected_versions?: Record<string, string[]>;
  best_rerank?: number | null;
  admissible?: boolean;
  top_chunk?: string | null;
  covered_by_question_context?: boolean;
  retrieved?: string[];
  status?: string;
  citations?: number[];
}

export interface Claim {
  text: string;
  citations: number[];
  quotes?: string[];
  intents?: number[];
  version_label?: string; // a claim of an answer split by document version
}

export interface Phase1Answer {
  question: string;
  status: string;
  text: string;
  decision: { strategy: string; reason: string; signals?: Record<string, unknown> };
  citations: Citation[];
  claims: Claim[];
  abstention_reason: string | null;
  abstention_detail: string;
  sources_considered: { label: string; chunk_id: string; retrieval_rank?: number; duplicate_of?: string | null; version?: string }[];
  model: string | null;
  provider: string | null;
  prompt_version: string;
  verification_problems: string[];
  not_in_sources: string[];
  verification_notes: string[];
  parts: { series_id?: string; doc_id?: string; label?: string }[];
}

export interface MultiIntentAnswer {
  question: string;
  status: string;
  text: string;
  strategy: string;
  reason: string;
  decomposition?: { question: string; intents: DecomposedIntent[]; multi: boolean; reason: string; flags: string[] };
  intents: IntentRecord[];
  citations: Citation[];
  claims: Claim[];
  abstention_reason: string | null;
  abstention_detail: string;
  not_in_sources: string[];
  verification_problems: string[];
  verification_notes: string[];
  evidence_intents: Record<string, { intent: number; rank: number; score: number | null }[]>;
  phase1: Phase1Answer[];
}

export interface TurnDetail {
  index: number;
  question: string;
  resolution: ResolutionView & { topic: string[]; signals: Record<string, unknown> };
  status: string;
  text: string;
  reused_citations: string[];
  answer: MultiIntentAnswer | null;
}

export interface SessionDetail {
  schema_version: number;
  session_id: string;
  turns: TurnDetail[];
}

// ---- POST /retrieve

export type RetrievalMode = "single" | "iterative";

export interface RetrievalRequest {
  query: string;
  mode: RetrievalMode;
  max_rounds: number;
  k: number;
  request_id?: string;
  session_id?: string;
}

export interface Chunk {
  chunk_id: string;
  doc_id: string;
  title: string | null;
  organization: string | null;
  document_type: string | null;
  domain: string | null;
  version: string | null;
  effective_date: string | null;
  superseded_date: string | null;
  is_current: boolean | null;
  series_id: string | null;
  source_url: string | null;
  page_start: number;
  page_end: number;
  pages: number[];
  section_path: string[];
  clause_id: string | null;
  text: string;
  token_count: number;
  content_hash: string;
}

export interface EvidenceItem {
  chunk: Chunk;
  rank: number;
  score: number | null;
  retriever: string;
  rerank_score: number | null;
}

export interface Coverage {
  decision: "sufficient" | "insufficient" | "structurally_unresolved" | string;
  reason: string;
  signals: Record<string, unknown>;
}

export interface Round {
  iteration: number;
  query: string;
  strategy: string;
  target: string | null;
  retrieved: string[];
  new: string[];
  excluded_versions: string[];
  retained: string[];
  context: string[];
  promoted: string[];
  coverage: Coverage;
  decision: string;
}

export interface RetrievalTrace {
  question: string;
  max_rounds: number;
  rounds: Round[];
  skipped: { strategy: string; query: string; why: string }[];
  stop_reason: string;
}

export interface RetrievalResponse {
  request_id: string | null;
  job_id: string | null;
  session_id: string | null;
  query: string;
  mode: RetrievalMode;
  evidence: EvidenceItem[];
  resolution: {
    kind: string;
    dates: { text: string }[];
    trigger: string;
    selected: Record<string, string[]>;
    dropped: string[];
    flags: string[];
    candidates: number;
  };
  trace: RetrievalTrace | null;
  seconds: Record<string, number>;
}
