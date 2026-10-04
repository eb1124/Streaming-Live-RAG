"""Context assembly: retrieved chunks -> numbered, provenance-preserving sources for the LLM.

Deterministic selection (documented in docs/generation.md):
  1. Keep retrieval order (the order the retriever/reranker produced). Never re-sort.
  2. Drop repeated chunk ids (a chunk is never included twice).
  3. Identical text in a different document (same content_hash, e.g. unchanged sections of the UConn
     February and July procedures) is kept as its own citable source, but its text is not repeated:
     the source says "identical to [Sk]". Both versions stay citable; the text costs tokens once.
  4. Add sources in order until MAX_SOURCES or MAX_CONTEXT_TOKENS would be exceeded. A chunk is never
     truncated: a chunk that does not fit is skipped and the next one is tried.
Each source keeps its full metadata (organization, title, dates, section path, clause, pages, chunk id)
and its text verbatim. Labels are S1..Sn in context order.

Versions: when the context holds sources from two or more documents of one series (series_id metadata,
e.g. the UConn procedures effective 2026-02-01 and 2026-07-01), `version_note` lists which sources belong
to which version, so the model can keep version-specific rules apart; validate.py enforces it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from chunking.models import Chunk
from chunking.tokens import count_tokens

from . import config as C


@dataclass(frozen=True)
class Evidence:
    """One retrieved chunk as handed over by the retrieval layer."""

    chunk: Chunk
    rank: int  # 1-based rank in the retriever's output
    score: float | None = None
    retriever: str = ""  # e.g. "hybrid-rrf", "hybrid-rrf-rerank"
    rerank_score: float | None = None  # cross-encoder logit, when reranking ran


@dataclass
class Source:
    label: str  # "S1"
    chunk: Chunk
    retrieval_rank: int
    duplicate_of: str | None = None  # label of the source whose text is identical

    @property
    def text(self) -> str:
        return self.chunk.text


@dataclass
class Context:
    sources: list[Source]
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (chunk_id, reason)
    source_tokens: int = 0

    def by_label(self) -> dict[str, Source]:
        return {s.label: s for s in self.sources}


def assemble(evidence: list[Evidence], max_sources: int = C.MAX_SOURCES,
             max_tokens: int = C.MAX_CONTEXT_TOKENS) -> Context:
    ordered = sorted(evidence, key=lambda e: e.rank)  # ranks are unique per retrieval run
    ctx = Context(sources=[])
    seen_ids: set[str] = set()
    first_with_hash: dict[str, str] = {}
    for ev in ordered:
        c = ev.chunk
        if c.chunk_id in seen_ids:
            ctx.skipped.append((c.chunk_id, "repeated chunk"))
            continue
        if len(ctx.sources) >= max_sources:
            ctx.skipped.append((c.chunk_id, "source limit"))
            continue
        dup = first_with_hash.get(c.content_hash)
        cost = 0 if dup else count_tokens(c.text)
        if ctx.source_tokens + cost > max_tokens:
            ctx.skipped.append((c.chunk_id, "token budget"))
            continue
        label = f"S{len(ctx.sources) + 1}"
        ctx.sources.append(Source(label, c, ev.rank, duplicate_of=dup))
        ctx.source_tokens += cost
        seen_ids.add(c.chunk_id)
        first_with_hash.setdefault(c.content_hash, label)
    return ctx


def source_header(s: Source) -> str:
    """One line of metadata, omitting every field that is unknown (never invented)."""
    c = s.chunk
    parts = [f"[{s.label}]"]
    if c.organization:
        parts.append(f"organization: {c.organization}")
    if c.title:
        parts.append(f"document: {c.title}")
    if c.document_type:
        parts.append(f"type: {c.document_type}")
    if c.effective_date:
        parts.append(f"effective: {c.effective_date}")
    if c.superseded_date:
        parts.append(f"superseded: {c.superseded_date}")
    if c.section_path:
        parts.append(f"section: {' > '.join(c.section_path)}")
    if c.clause_id:
        parts.append(f"clause: {c.clause_id}")
    parts.append(f"pages: {c.page_start}" if c.page_start == c.page_end else f"pages: {c.page_start}-{c.page_end}")
    parts.append(f"chunk: {c.chunk_id}")
    return " | ".join(parts)


def render_sources(ctx: Context) -> str:
    blocks = []
    for s in ctx.sources:
        body = f"(identical text to [{s.duplicate_of}]; not repeated)" if s.duplicate_of else s.text
        blocks.append(f"{source_header(s)}\n{body}")
    return "\n\n".join(blocks)


def version_groups(ctx: Context) -> dict[str, dict[str, list[str]]]:
    """series_id -> doc_id -> labels, only for series with two or more documents in the context.
    Documents are ordered by effective date (then doc_id), labels by context order."""
    groups: dict[str, dict[str, list[str]]] = {}
    for s in ctx.sources:
        if s.chunk.series_id:
            groups.setdefault(s.chunk.series_id, {}).setdefault(s.chunk.doc_id, []).append(s.label)
    first = {s.chunk.doc_id: s.chunk for s in reversed(ctx.sources)}
    return {sid: dict(sorted(docs.items(), key=lambda kv: (first[kv[0]].effective_date or "", kv[0])))
            for sid, docs in groups.items() if len(docs) >= 2}


def version_note(ctx: Context) -> str:
    """Plain-text listing of document versions in the context ("" when there is none)."""
    by_label = ctx.by_label()
    lines = []
    for docs in version_groups(ctx).values():
        c = by_label[next(iter(docs.values()))[0]].chunk
        name = " / ".join(x for x in (c.organization, c.title) if x) or c.series_id
        lines.append(f"- {name}: {len(docs)} versions of the same document; rules can differ between them.")
        for labels in docs.values():
            v = by_label[labels[0]].chunk
            dates = f"effective {v.effective_date or 'unknown'}"
            if v.superseded_date:
                dates += f", superseded {v.superseded_date}"
            if v.is_current is True:
                dates += ", current"
            lines.append(f"  - version {dates}: {', '.join(labels)}")
    return "Document versions:\n" + "\n".join(lines) if lines else ""
