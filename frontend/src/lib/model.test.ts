import { describe, expect, it } from "vitest";
import type { Citation, QueryResponse, RetrievalResponse, TurnDetail } from "../api/types";
import {
  abstentionTitle,
  answerByIntent,
  answerByOrganization,
  claimSentence,
  claimsFor,
  evidenceEntry,
  evidenceRetrievedOnly,
  evidenceShown,
  inspectionTargets,
  markQuotes,
  outcomeOf,
  runState,
  segments,
  targetsFor,
  thread,
  words,
  type Run,
} from "./model";
import { newTraceparent, parseTraceHeader } from "./trace";

const cite = (number: number, chunk_id: string): Citation => ({
  number,
  label: `S${number}`,
  chunk_id,
  doc_id: "d",
  organization: "Org",
  document: "Doc",
  effective_date: null,
  pages: "3",
  section: null,
  clause: null,
});

describe("segments", () => {
  it("splits text at citation markers and keeps only markers that are citations", () => {
    const out = segments("Due in 30 days [1]. See [2, 1] and [9] and [x].", [cite(1, "a"), cite(2, "b")]);
    expect(out.filter((s) => s.kind === "marker").map((s) => (s as { number: number }).number)).toEqual([1, 2, 1]);
    expect(out.map((s) => (s.kind === "text" ? s.text : `<${s.number}>`)).join("")).toBe("Due in 30 days <1>. See <2><1> and [9] and [x].");
  });

  it("returns the text unchanged when there are no citations", () => {
    expect(segments("No sources [1].", [])).toEqual([{ kind: "text", text: "No sources [1]." }]);
  });
});

describe("trace context", () => {
  it("makes a valid, random traceparent", () => {
    const a = newTraceparent();
    const b = newTraceparent();
    expect(a).toMatch(/^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/);
    expect(a).not.toBe(b);
    expect(parseTraceHeader(a)).toEqual({ traceId: a.split("-")[1], spanId: a.split("-")[2] });
  });

  it("rejects anything that is not a trace header", () => {
    for (const bad of [null, undefined, "", "abc", "00-xyz-123-01", "00-" + "0".repeat(31) + "-" + "0".repeat(16) + "-01"]) {
      expect(parseTraceHeader(bad)).toBeNull();
    }
  });
});

describe("evidence view model", () => {
  const response = { evidence: ["a", "b"], citations: [cite(1, "b")] } as unknown as QueryResponse;
  const turn = {
    answer: {
      evidence_intents: { a: [{ intent: 0, rank: 1, score: 2 }] },
      phase1: [{ sources_considered: [{ label: "S1", chunk_id: "a" }, { label: "S2", chunk_id: "b" }] }],
    },
  } as unknown as TurnDetail;
  const result = {
    evidence: [
      { chunk: { chunk_id: "b", text: "text of b" }, rank: 1, score: 1, retriever: "r", rerank_score: 1 },
      { chunk: { chunk_id: "c", text: "text of c" }, rank: 2, score: 0, retriever: "r", rerank_score: 0 },
    ],
    trace: { rounds: [{ iteration: 1, new: ["c"], promoted: [] }, { iteration: 2, new: ["b"], promoted: ["b"] }] },
  } as unknown as RetrievalResponse;
  const run: Run = {
    id: "1",
    question: "q",
    sessionId: "s",
    requestId: "r",
    startedAt: "2026-01-01T00:00:00Z",
    pending: false,
    response,
    turn,
    inspections: { question: { key: "question", label: "Whole question", request: { query: "q", mode: "iterative", max_rounds: 3, k: 10 }, pending: false, result } },
  };

  it("lists exactly the chunks of the query response, in its order, and never invents text", () => {
    const shown = evidenceShown(run);
    expect(shown.map((e) => e.chunkId)).toEqual(["a", "b"]);
    expect(shown[0]).toMatchObject({ label: "S1", item: null, firstRound: null, promoted: false, citations: [] });
    expect(shown[0].intents).toEqual([{ intent: 0, rank: 1, score: 2 }]);
    expect(shown[1]).toMatchObject({ label: "S2", firstRound: 2, promoted: true, itemFrom: "Whole question" });
    expect(shown[1].item?.chunk.text).toBe("text of b");
    expect(shown[1].citations.map((c) => c.number)).toEqual([1]);
  });

  it("separates chunks that were retrieved but not shown", () => {
    expect(evidenceRetrievedOnly(run).map((e) => [e.chunkId, e.firstRound])).toEqual([["c", 1]]);
  });

  it("has nothing to show for a run without a response", () => {
    expect(evidenceShown({ ...run, response: undefined })).toEqual([]);
  });
});

describe("a turn read from the stored session", () => {
  const citation = cite(1, "b");
  const turn = {
    index: 2,
    question: "q",
    status: "answered",
    text: "Text [1].",
    reused_citations: [],
    resolution: { kind: "follow_up", reason: "r", query: "standalone q", temporal: "", organizations: ["Org"], anchor: 1, topic: [], signals: {} },
    answer: {
      strategy: "per_intent",
      abstention_reason: null,
      citations: [citation],
      claims: [
        { text: "first", citations: [1], quotes: ["text of b"], intents: [1] },
        { text: "second", citations: [], quotes: [], intents: [0] },
      ],
      decomposition: { multi: true },
      intents: [
        { index: 0, text: "i0", sub_query: "sub 0", organizations: [], retrieved: ["a"] },
        { index: 1, text: "i1", sub_query: "sub 1", organizations: [], retrieved: ["b", "c"] },
      ],
      evidence_intents: { b: [{ intent: 1, rank: 1, score: 3 }] },
      phase1: [
        { sources_considered: [{ label: "S1", chunk_id: "a", retrieval_rank: 4 }] },
        { sources_considered: [{ label: "S1", chunk_id: "b", retrieval_rank: 1 }, { label: "S2", chunk_id: "a" }] },
      ],
    },
  } as unknown as TurnDetail;
  const stored: Run = { id: "stored:s:2", question: "q", sessionId: "s", pending: false, stored: true, turn, inspections: {} };

  it("has the outcome POST /query would have returned for it", () => {
    expect(outcomeOf(stored)).toEqual({
      answered: true,
      status: "answered",
      text: "Text [1].",
      citations: [citation],
      evidence: ["a", "b"],
      abstentionReason: null,
      resolution: turn.resolution,
      strategy: "per_intent",
      turnIndex: 2,
    });
    expect(runState(stored)).toBe("answered");
  });

  it("reports an unresolved turn as an abstention with no evidence", () => {
    const unresolved = { ...stored, turn: { ...turn, status: "abstained", answer: null } };
    expect(outcomeOf(unresolved)).toMatchObject({ answered: false, abstentionReason: "unresolved_reference", evidence: [], citations: [], strategy: null });
  });

  it("lists its evidence with the stored label, rank and citations", () => {
    const shown = evidenceShown(stored);
    expect(shown.map((e) => [e.chunkId, e.label, e.retrievalRank])).toEqual([["a", "S1", 4], ["b", "S1", 1]]);
    expect(shown[1].citations).toEqual([citation]);
    expect(shown[1]).toMatchObject({ multi: true, item: null, shownToModel: true });
    expect(evidenceEntry(stored, "zzz")).toMatchObject({ shownToModel: false, label: null, citations: [], intents: [] });
  });

  it("finds the claims a chunk supports through the citations", () => {
    expect(claimsFor(stored, "b").map((c) => [c.number, c.claim.text])).toEqual([[1, "first"]]);
    expect(claimsFor(stored, "a")).toEqual([]);
  });

  it("names the retrievals that can return a chunk's text", () => {
    expect(inspectionTargets(stored).map((t) => [t.key, t.query])).toEqual([["question", "standalone q"], ["intent-0", "sub 0"], ["intent-1", "sub 1"]]);
    expect(targetsFor(stored, ["c"]).map((t) => t.key)).toEqual(["intent-1"]);
    expect(targetsFor(stored, ["nowhere"])).toEqual([]);
  });

  it("is not an outcome unless the run is marked as stored", () => {
    expect(outcomeOf({ ...stored, stored: false })).toBeNull();
    expect(runState({ ...stored, stored: false })).toBe("error");
  });
});

describe("run outcomes", () => {
  const base: Run = { id: "1", question: "q", sessionId: "s", pending: false, inspections: {} };

  it("tells running, failed and unanswered runs apart", () => {
    expect(runState({ ...base, pending: true })).toBe("pending");
    expect(runState({ ...base, error: "unreachable" })).toBe("error");
    const failed = { status: "failed", error: { type: "X" } } as unknown as QueryResponse;
    expect(runState({ ...base, response: failed })).toBe("failed");
    expect(outcomeOf({ ...base, response: failed })).toBeNull();
    const abstained = { status: "completed", answer_status: "abstained", evidence: [], citations: [], turn_index: 0 } as unknown as QueryResponse;
    expect(runState({ ...base, response: abstained })).toBe("abstained");
  });

  it("orders a session by turn index and keeps a run without one where it was asked", () => {
    const at = (id: string, index: number | null, sessionId = "s"): Run => ({
      ...base,
      id,
      sessionId,
      response: index == null ? undefined : ({ status: "completed", turn_index: index } as unknown as QueryResponse),
    });
    const runs = [at("c", 2), at("failed", null), at("other", 0, "t"), at("a", 0), at("b", 1)];
    expect(thread(runs, "s").map((r) => r.id)).toEqual(["a", "b", "c", "failed"]);
    expect(thread(runs, "t").map((r) => r.id)).toEqual(["other"]);
  });
});

describe("quotes in evidence text", () => {
  it("marks a quote only where it occurs verbatim, whitespace aside", () => {
    const text = "Cards are suspended\nafter 60 days. Fees apply (see 4.1).";
    expect(markQuotes(text, ["suspended after 60 days", "(see 4.1)"])).toEqual([
      { text: "Cards are ", quoted: false },
      { text: "suspended\nafter 60 days", quoted: true },
      { text: ". Fees apply ", quoted: false },
      { text: "(see 4.1)", quoted: true },
      { text: ".", quoted: false },
    ]);
  });

  it("marks nothing for a quote that is not in the text, and never changes the text", () => {
    const text = "Cards are suspended after 60 days.";
    expect(markQuotes(text, ["suspended after 90 days", ""])).toEqual([{ text, quoted: false }]);
    const parts = markQuotes(text, ["Cards are suspended", "suspended after"]);
    expect(parts.map((p) => p.text).join("")).toBe(text);
    expect(parts.filter((p) => p.quoted).map((p) => p.text)).toEqual(["Cards are suspended", " after"]);
  });
});

describe("a multi-intent answer, intent by intent", () => {
  const intents = [
    { index: 0, text: "UConn rule?", sub_query: "s0", organizations: ["University of Connecticut"], status: "not_answered", admissible: true },
    { index: 1, text: "Rutgers rule?", sub_query: "s1", organizations: ["Rutgers University"], status: "answered", admissible: true },
  ];
  const of = (answer: object): Run => ({
    id: "1",
    question: "q",
    sessionId: "s",
    pending: false,
    stored: true,
    turn: { index: 0, question: "q", status: "answered", text: "t", reused_citations: [], resolution: {}, answer: { decomposition: { multi: true }, intents, citations: [cite(1, "b")], phase1: [], ...answer } } as unknown as TurnDetail,
    inspections: {},
  });

  it("puts each stored claim under the intents the backend attributes it to, and invents none", () => {
    const run = of({
      claims: [
        { text: "Advances need approval", citations: [1], quotes: [], intents: [1] },
        { text: "Unplaced claim.", citations: [1], quotes: [], intents: [] },
      ],
      not_in_sources: ["the UConn suspension rule"],
    });
    const out = answerByIntent(run)!;
    expect(out.intents.map((i) => [i.intent.index, i.claims.map((c) => c.number)])).toEqual([[0, []], [1, [1]]]);
    expect(out.unattributed.map((c) => c.number)).toEqual([2]);
    expect(out.gaps).toEqual(["the UConn suspension rule"]);
  });

  it("files a gap the per-intent strategy labels with a part under that intent", () => {
    const run = of({ claims: [], not_in_sources: ["Part 1 (UConn rule?): no verified answer (low_relevance)", "something general"] });
    const out = answerByIntent(run)!;
    expect(out.intents.map((i) => i.gaps)).toEqual([["no verified answer (low_relevance)"], []]);
    expect(out.gaps).toEqual(["something general"]);
  });

  it("applies only to a question the backend split", () => {
    expect(answerByIntent(of({ decomposition: { multi: false }, claims: [] }))).toBeNull();
    expect(answerByIntent(of({ decomposition: undefined, claims: [] }))).toBeNull();
    expect(answerByIntent({ ...of({ claims: [] }), turn: undefined })).toBeNull();
  });

  it("groups a one-intent answer about several organizations by the organization of the cited sources", () => {
    const citations = [{ ...cite(1, "b"), organization: "Rutgers University" }, { ...cite(2, "c"), organization: "Rutgers University" }, { ...cite(3, "d"), organization: null }];
    const run = of({
      decomposition: { multi: false },
      intents: [intents[0]],
      citations,
      claims: [
        { text: "Advances are discouraged", citations: [1], intents: [0] },
        { text: "Excess funds are returned", citations: [2, 1], intents: [0] },
        { text: "No organization on this source", citations: [3], intents: [0] },
      ],
      not_in_sources: ["UConn University Travel Card suspension rule"],
    });
    run.turn!.resolution.organizations = ["University of Connecticut", "Rutgers University"];
    const out = answerByOrganization(run)!;
    expect(out.organizations.map((o) => [o.organization, o.claims.map((c) => c.number)])).toEqual([
      ["University of Connecticut", []],
      ["Rutgers University", [1, 2]],
    ]);
    expect(out.other.map((c) => c.number)).toEqual([3]);
    expect(out.gaps).toEqual(["UConn University Travel Card suspension rule"]);
  });

  it("does not group by organization when the backend split the question, one organization is named, or it abstained", () => {
    const split = of({ claims: [] });
    split.turn!.resolution.organizations = ["A", "B"];
    expect(answerByOrganization(split)).toBeNull();
    const one = of({ decomposition: { multi: false }, claims: [] });
    one.turn!.resolution.organizations = ["A"];
    expect(answerByOrganization(one)).toBeNull();
    const abstained = of({ decomposition: { multi: false }, claims: [] });
    abstained.turn!.resolution.organizations = ["A", "B"];
    abstained.turn!.status = "abstained";
    expect(answerByOrganization(abstained)).toBeNull();
  });

  it("writes a claim as the answer text does", () => {
    expect(claimSentence({ text: "Advances need approval", citations: [1] })).toBe("Advances need approval.");
    expect(claimSentence({ text: "Is it so?", citations: [] })).toBe("Is it so?");
    expect(claimSentence({ text: "Cards lapse.", citations: [1], version_label: "July 2026" })).toBe("July 2026: Cards lapse.");
  });
});

describe("codes", () => {
  it("shows an unknown code as it is and never hides one", () => {
    expect(words("some_new_code")).toBe("some new code");
    expect(words(null)).toBe("—");
    expect(abstentionTitle("provider_error")).toBe("Model provider error");
    expect(abstentionTitle("model_insufficient_evidence")).toBe("Insufficient evidence");
    expect(abstentionTitle("unresolved_reference")).toBe("Unresolved reference");
  });
});
