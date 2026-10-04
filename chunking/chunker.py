"""Structure-aware chunking of one ingested Document.

Strategy (docs/chunking.md has the full rationale):

1. Retrievable blocks (ingestion's `is_retrievable`, minus detected tables of contents) are
   arranged in a structure tree of headings and numbered clauses (chunking.tree).
2. A clause is atomic: kept whole, with its sub-items and sub-clauses, when it fits MAX_TOKENS.
   A heading section is split into its subsections; its own text before the first subsection is
   a unit of its own.
3. Adjacent small units under the same parent are grouped (sibling clauses below SMALL_TOKENS,
   heading sections only when tiny), up to TARGET_TOKENS. Units never merge across parents.
4. A unit that exceeds MAX_TOKENS is split at paragraph boundaries, then list-item boundaries,
   then after a list lead-in, then sentence boundaries, then clause punctuation, then words.
   Fragments of a split clause or list carry its head ("... the following methods:") as a
   lead-in: the only overlap between chunks.
5. Scrambled borderless tables (grid regions) are isolated into their own low-confidence chunks.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from ingestion.models import Block, Document, Line
from ingestion.metadata import LABELS
from ingestion.structure import is_retrievable, section_path as ingestion_section_path

from . import config as C
from .models import Chunk, TableRef
from .render import CLAUSE_END, SENTENCE_END, assess_table, boundary_spans, continues, render_table
from .tokens import count_tokens, split_tokens
from .tree import Node, Tree, build_tree, detect_navigation, norm

DATE_LABELS = {k: v for k, v in LABELS.items() if v.endswith("_date")}
PARA, LIST, WEAK, NEVER = 1, 2, 3, 4  # break preference between two adjacent blocks
LEVEL_NAME = {PARA: "paragraph", LIST: "list", WEAK: "list"}


@dataclass
class Unit:
    kind: str  # heading | clause | content
    tokens: int  # content tokens


@dataclass(eq=False)
class Piece:
    node: Node  # deepest structural node containing all of the piece's blocks
    blocks: list[Block]  # reading order; may include headings of nested sections
    kind: str  # heading | clause | content
    split: str = "structural"
    sealed: bool = False  # a fragment of a split unit: never grouped further
    units: list[Unit] = field(default_factory=list)
    text_override: str | None = None  # sentence/token/table-row fragments
    char_span: tuple[int, int] | None = None
    table_part: TableRef | None = None
    reasons: list[str] = field(default_factory=list)


@dataclass
class DocResult:
    chunks: list[Chunk]
    excluded: list[Chunk]
    diagnostics: list[dict]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DocChunker:
    def __init__(self, doc: Document):
        self.doc = doc
        self.all_blocks = list(doc.iter_blocks())
        self.order = {b.block_id: i for i, b in enumerate(self.all_blocks)}
        retrievable = [b for b in self.all_blocks if is_retrievable(b)]
        self.nav_ids = detect_navigation(retrievable, self.all_blocks)
        self.blocks = [b for b in retrievable if b.block_id not in self.nav_ids]
        self.tree: Tree = build_tree(self.blocks)
        self.lines: dict[str, Line] = {ln.line_id: ln for p in doc.pages for ln in p.lines}
        self.out_of_sequence = {d["block_id"] for d in self.tree.diagnostics if d["code"] == "out_of_sequence_clause_number"}
        self._tables: dict[str, tuple[str, list[str]]] = {}
        md = doc.metadata
        self.title = md.title.value
        self.organization = md.organization.value

    # ------------------------------------------------------------ rendering

    def table_info(self, b: Block) -> tuple[str, list[str]]:
        if b.block_id not in self._tables:
            self._tables[b.block_id] = assess_table(b.table.rows) if b.table else ("low", ["no structured rows"])
        return self._tables[b.block_id]

    def block_text(self, b: Block) -> str:
        if b.kind == "table" and b.table:
            conf, _ = self.table_info(b)
            return render_table(b.table.rows, conf, b.text)
        return b.text

    def path_heading_ids(self, node: Node) -> set[str]:
        return {n.block.block_id for n in node.ancestors() if n.is_heading_like}

    def text_blocks(self, piece: Piece) -> list[Block]:
        skip = self.path_heading_ids(piece.node)
        return [b for b in piece.blocks if b.block_id not in skip]

    def join(self, blocks: list[Block]) -> tuple[str, list[tuple[Block, int, int]]]:
        """Joined text plus each block's (start, end) offsets in it."""
        text, spans, prev = "", [], None
        for b in blocks:
            t = self.block_text(b)
            if text:
                text += " " if prev is not None and continues(prev, b) else "\n"
            spans.append((b, len(text), len(text) + len(t)))
            text += t
            prev = b
        return text, spans

    def render(self, piece: Piece) -> str:
        if piece.text_override is not None:
            return piece.text_override
        return self.join(self.text_blocks(piece))[0]

    def header(self, node: Node) -> str:
        path = node.path()
        if path and self.title and norm(path[0]) == norm(self.title):
            path = path[1:]
        top = self.title or "(untitled)"
        if self.organization:
            top += f" ({self.organization})"
        return top + ("\n" + " > ".join(path) if path else "")

    def lead_in(self, piece: Piece) -> str | None:
        """Context carried into a fragment: the head of the clause it belongs to and/or the
        '...:' line that introduces its list, when those are in an earlier chunk."""
        ids = {b.block_id for b in piece.blocks}
        found: list[Block] = []
        clause = piece.node.nearest_clause()
        if clause is not None and clause.block.block_id not in ids and not clause.title_only:
            found.append(clause.block)
        text_blocks = self.text_blocks(piece)
        first = text_blocks[0] if text_blocks else None
        if first is not None:
            # walk back over the list this fragment starts inside, to the line that introduces it
            prev = self.tree.prev_in_node.get(first.block_id)
            if first.kind == "list_item" or continues(prev, first) if prev is not None else False:
                while prev is not None and prev.kind == "list_item" and not prev.text.rstrip().endswith(":"):
                    prev = self.tree.prev_in_node.get(prev.block_id)
            if prev is not None and prev.block_id not in ids and prev.text.rstrip().endswith(":") and prev not in found:
                found.append(prev)
        if not found:
            return None
        parts = []
        for b in found:
            t = b.text
            if count_tokens(t) > C.LEAD_IN_MAX_TOKENS:
                t = split_tokens(t, C.LEAD_IN_MAX_TOKENS)[0] + " …"
            parts.append(t)
        return "\n".join(parts)

    def ctok(self, piece: Piece) -> int:
        return count_tokens(self.render(piece))

    def rtok(self, piece: Piece) -> int:
        lead = self.lead_in(piece)
        return count_tokens(self.header(piece.node)) + (count_tokens(lead) if lead else 0) + self.ctok(piece)

    # ------------------------------------------------------------ structure

    def piece(self, node: Node, blocks: list[Block], kind: str = "content", **kw) -> Piece:
        p = Piece(node, list(blocks), kind, **kw)
        if not p.units:
            p.units = [Unit(kind, self.ctok(p))]
        return p

    def chunk_node(self, node: Node) -> list[Piece]:
        kind = "content" if node.kind == "root" else node.kind
        whole = self.piece(node, node.subtree_blocks(), kind)
        if not self.render(whole).strip():
            # An empty heading (e.g. ingestion split "RESPONSIBLE OFFICE" into two headings) can
            # still join a tiny following sibling; alone it produces no chunk.
            return [whole] if node.kind == "heading" else []
        has_grid = any(b.block_id in self.tree.grid_block_ids for b in whole.blocks)
        if not has_grid and (node.kind == "clause" or not node.child_nodes()) and self.rtok(whole) <= C.MAX_TOKENS:
            return [whole]

        parts: list[list[Piece]] = []
        run: list[Block] = []
        for item in node.items:
            if isinstance(item, Node):
                if run:
                    parts.append(self.split_run(run, node))
                    run = []
                sub = self.chunk_node(item)
                if sub:
                    parts.append(sub)
            else:
                run.append(item)
        if run:
            parts.append(self.split_run(run, node))
        pieces = self.group(parts, node)

        content_ids = {b.block_id for b in self.text_blocks(whole)}
        if len(pieces) == 1 and not pieces[0].sealed and {b.block_id for b in self.text_blocks(pieces[0])} >= content_ids:
            only = pieces[0]
            only.blocks = whole.blocks  # incl. this node's heading, which a merge must render inline
            only.kind = kind
            only.units = [Unit(kind, self.ctok(only))]
            return [only]
        for p in pieces:
            p.sealed = True
        return pieces

    def can_merge(self, a: Piece, b: Piece, node: Node, next_tiny: bool) -> Piece | None:
        """Heading sections merge only when one side is too thin to stand alone: a tiny section
        attaches to the group before it, a tiny group absorbs the next section, and an empty
        heading joins only if its own (tiny) content follows. Clause and content units merge when
        one side is small. Either way the group must stay within TARGET/MAX."""
        last, first = a.units[-1], b.units[0]
        acc_tokens = sum(u.tokens for u in a.units)
        if last.kind == "heading" or first.kind == "heading":
            if first.tokens == 0:
                ok = next_tiny
            else:
                ok = first.tokens < C.TINY_TOKENS or 0 < acc_tokens < C.TINY_TOKENS
        else:
            ok = last.tokens < C.SMALL_TOKENS or first.tokens < C.SMALL_TOKENS
        if not ok:
            return None
        merged = Piece(node, a.blocks + b.blocks, "content", split="grouped", units=a.units + b.units,
                       reasons=a.reasons + b.reasons)
        if self.ctok(merged) > C.TARGET_TOKENS or self.rtok(merged) > C.MAX_TOKENS:
            return None
        return merged

    def group(self, parts: list[list[Piece]], node: Node) -> list[Piece]:
        out: list[Piece] = []
        acc: Piece | None = None
        for i, plist in enumerate(parts):
            if len(plist) == 1 and not plist[0].sealed:
                p = plist[0]
                nxt = parts[i + 1] if i + 1 < len(parts) else None
                next_tiny = bool(nxt) and len(nxt) == 1 and not nxt[0].sealed and 0 < nxt[0].units[0].tokens < C.TINY_TOKENS
                merged = self.can_merge(acc, p, node, next_tiny) if acc is not None else None
                if merged is not None:
                    acc = merged
                else:
                    if acc is not None:
                        out.append(acc)
                    acc = p
            else:
                if acc is not None:
                    out.append(acc)
                    acc = None
                out.extend(plist)
        if acc is not None:
            out.append(acc)
        return out

    # ------------------------------------------------------------ size fallbacks

    def split_run(self, run: list[Block], node: Node) -> list[Piece]:
        """A run of a node's own content blocks (no sub-nodes). Grid regions become separate pieces."""
        segments: list[tuple[bool, list[Block]]] = []
        for b in run:
            is_grid = b.block_id in self.tree.grid_block_ids
            if segments and segments[-1][0] == is_grid:
                segments[-1][1].append(b)
            else:
                segments.append((is_grid, [b]))
        pieces: list[Piece] = []
        for is_grid, seg in segments:
            ps = self.fallback(seg, node)
            if is_grid:
                for p in ps:
                    p.reasons.append("unreconstructed_table_region")
            pieces.extend(ps)
        if len(pieces) > 1:
            for p in pieces:
                p.sealed = True
        return pieces

    def break_level(self, prev: Block, cur: Block) -> int:
        if continues(prev, cur):
            return NEVER
        if prev.text.rstrip().endswith(":"):
            return WEAK  # keep a lead-in with the list it introduces
        if prev.kind == "list_item" and cur.kind == "list_item":
            return LIST
        return PARA

    def fallback(self, blocks: list[Block], node: Node) -> list[Piece]:
        whole = self.piece(node, blocks)
        if self.rtok(whole) <= C.MAX_TOKENS:
            return [whole]
        if len(blocks) == 1 and blocks[0].kind == "table" and blocks[0].table:
            return self.split_table(blocks[0], node)
        levels = [self.break_level(blocks[i - 1], blocks[i]) for i in range(1, len(blocks))]
        for level in (PARA, LIST, WEAK):
            cuts = [i for i, lv in enumerate(levels, 1) if lv <= level]
            if not cuts:
                continue
            segs = [blocks[a:b] for a, b in zip([0] + cuts, cuts + [len(blocks)])]
            out: list[Piece] = []
            cur: list[Block] = []
            for seg in segs:
                if self.rtok(self.piece(node, seg)) > C.MAX_TOKENS:
                    if cur:
                        out.append(self.piece(node, cur, split=LEVEL_NAME[level]))
                        cur = []
                    out.extend(self.fallback(seg, node))
                    continue
                if cur:
                    trial = self.piece(node, cur + seg)
                    if self.ctok(trial) > C.TARGET_TOKENS or self.rtok(trial) > C.MAX_TOKENS:
                        out.append(self.piece(node, cur, split=LEVEL_NAME[level]))
                        cur = []
                cur = cur + seg
            if cur:
                out.append(self.piece(node, cur, split=LEVEL_NAME[level]))
            for p in out:
                p.sealed = True
            return out
        return self.split_text(blocks, node)

    def split_text(self, blocks: list[Block], node: Node) -> list[Piece]:
        """Blocks that cannot be separated (one block, or a sentence broken across blocks):
        split their joined text at sentence boundaries, then ';'/':' boundaries, then words."""
        text, spans = self.join(blocks)
        budget = C.TARGET_TOKENS

        def fits(s: int, e: int) -> bool:
            p = self.piece(node, [b for b, bs, be in spans if bs < e and be > s], text_override=text[s:e])
            return self.ctok(p) <= budget and self.rtok(p) <= C.MAX_TOKENS

        def units(s: int, e: int, pattern) -> list[tuple[int, int, str]]:
            out = []
            for a, b in boundary_spans(text[s:e], pattern):
                a, b = a + s, b + s
                if fits(a, b):
                    out.append((a, b, "sentence"))
                elif pattern is SENTENCE_END:
                    out.extend(units(a, b, CLAUSE_END))
                else:  # one clause longer than the budget: word windows (last resort)
                    out.extend(word_windows(a, b))
            return out

        def word_windows(a: int, b: int) -> list[tuple[int, int, str]]:
            size = min(budget, C.MAX_TOKENS - count_tokens(self.header(node)) - 2 * C.LEAD_IN_MAX_TOKENS)
            out, start, end, n = [], None, None, 0
            for m in re.finditer(r"\S+", text[a:b]):
                ws, we = m.start() + a, m.end() + a
                k = count_tokens(text[ws:we])
                if start is not None and n + k > size:
                    out.append((start, end, "token"))
                    start, n = None, 0
                if start is None:
                    start = ws
                n += k
                end = we
            if start is not None:
                out.append((start, end, "token"))
            return out

        pieces: list[Piece] = []
        cur: tuple[int, int, str] | None = None
        for s, e, how in units(0, len(text), SENTENCE_END):
            if cur is not None and how == "sentence" and cur[2] == "sentence" and fits(cur[0], e):
                cur = (cur[0], e, "sentence")
                continue
            if cur is not None:
                pieces.append(self._text_piece(node, text, spans, *cur))
            cur = (s, e, how)
        if cur is not None:
            pieces.append(self._text_piece(node, text, spans, *cur))
        for p in pieces:
            p.sealed = True
        return pieces

    def _text_piece(self, node, text, spans, s, e, how) -> Piece:
        blocks = [b for b, bs, be in spans if bs < e and be > s]
        p = self.piece(node, blocks, text_override=text[s:e].strip(), char_span=(s, e), split=how)
        p.reasons.append("token_level_split" if how == "token" else "sentence_level_split")
        return p

    def split_table(self, block: Block, node: Node) -> list[Piece]:
        """An oversized table: split between rows; each fragment repeats the header row."""
        conf, notes = self.table_info(block)
        header, *body = block.table.rows
        original_lines = block.text.split("\n")
        pieces, start = [], 0
        while start < len(body):
            end = start + 1
            while end < len(body):
                trial = self._table_piece(node, block, header, body, start, end + 1, conf, notes, original_lines)
                if self.ctok(trial) > C.TARGET_TOKENS or self.rtok(trial) > C.MAX_TOKENS:
                    break
                end += 1
            pieces.append(self._table_piece(node, block, header, body, start, end, conf, notes, original_lines))
            start = end
        for p in pieces:
            p.sealed = True
        return pieces

    def _table_piece(self, node, block, header, body, start, end, conf, notes, original_lines) -> Piece:
        rows = [header] + body[start:end]
        if conf == "low":  # keep ingestion's own row text
            text = "\n".join([original_lines[0]] + original_lines[1 + start: 1 + end])
        else:
            text = render_table(rows, conf, block.text)
        ref = TableRef(block_id=block.block_id, rows=body[start:end], header_row=header, confidence=conf,
                       notes=notes + [f"rows {start + 1}-{end} of {len(body)}"])
        return self.piece(node, [block], text_override=text, split="table_rows", table_part=ref,
                          char_span=(start + 1, end))

    # ------------------------------------------------------------ output

    def metadata(self) -> dict:
        md = self.doc.metadata

        def date(f):
            return f.normalized or f.value

        return {
            "organization": md.organization.value,
            "title": md.title.value,
            "domain": md.domain.value,
            "document_type": md.document_type.value,
            "authority_level": md.authority_level.value,
            "version": md.version.value,
            "issued_date": date(md.issued_date),
            "effective_date": date(md.effective_date),
            "superseded_date": date(md.superseded_date),
            "is_current": {"true": True, "false": False}.get(md.is_current.value or ""),
            "series_id": md.series_id.value,
            "source_url": md.source_url.value,
            "capture_date": date(md.captured_at),
        }

    def line_ids(self, blocks_spans, s: int | None, e: int | None) -> list[str]:
        out = []
        for b, bs, be in blocks_spans:
            if s is not None and not (bs < e and be > s):
                continue
            if s is None or b.kind == "table" or (s <= bs and be <= e):
                out.extend(b.line_ids)
                continue
            pos = 0  # locate each line inside the block text to keep only lines in [s, e)
            for lid in b.line_ids:
                lt = self.lines[lid].text
                idx = b.text.find(lt, pos) if lt else -1
                if idx < 0:
                    out.append(lid)  # cannot locate: keep (over-inclusive, never lossy)
                    continue
                ls, le = bs + idx, bs + idx + len(lt)
                pos = idx + len(lt)
                if ls < e and le > s:
                    out.append(lid)
        return out

    def to_chunk(self, piece: Piece, meta: dict) -> Chunk | None:
        text = self.render(piece).strip()
        if not text:
            return None
        blocks = self.text_blocks(piece)
        lead = self.lead_in(piece)
        header = self.header(piece.node)
        retrieval = header + "\n" + (lead + "\n" if lead else "") + text
        pages = sorted({b.page for b in blocks})

        _, spans = self.join(blocks)
        if piece.split == "table_rows":
            line_ids = list(blocks[0].line_ids)
        elif piece.text_override is not None and piece.char_span:
            line_ids = self.line_ids(spans, *piece.char_span)
        else:
            line_ids = self.line_ids(spans, None, None)

        reasons = list(piece.reasons)
        tables = []
        for b in blocks:
            if b.kind == "table" and b.table:
                conf, notes = self.table_info(b)
                if piece.table_part is None:
                    tables.append(TableRef(block_id=b.block_id, rows=b.table.rows, confidence=conf, notes=notes))
                if conf == "low":
                    reasons.append("table_low_confidence")
        if piece.table_part is not None:
            tables.append(piece.table_part)
        nodes = {id(self.tree.node_of[b.block_id]): self.tree.node_of[b.block_id] for b in blocks}
        nodes[id(piece.node)] = piece.node
        if any(a.suspect for n in nodes.values() for a in n.ancestors()):
            reasons.append("possible_wrong_section_path")
        if any(b.block_id in self.out_of_sequence for b in blocks):
            reasons.append("out_of_sequence_clause_number")
        if any(b.kind == "heading" and b.confidence == "low" for b in blocks) or any(
            n.kind == "heading" and n.block.confidence == "low" for n in piece.node.ancestors()
        ):
            reasons.append("low_confidence_heading")
        if piece.split == "list" and any(b.list_type == "implicit" for b in blocks):
            reasons.append("split_at_low_confidence_list_boundary")
        reasons = list(dict.fromkeys(reasons))
        low = {"unreconstructed_table_region", "table_low_confidence", "possible_wrong_section_path", "token_level_split"}
        confidence = "low" if low & set(reasons) else ("medium" if reasons else "high")

        clause = piece.node.nearest_numbered()
        clause_ids = []
        for b in blocks:
            c = self.tree.node_of[b.block_id].nearest_numbered()
            if c is not None and c.number.label not in clause_ids:
                clause_ids.append(c.number.label)
        block_ids = [b.block_id for b in blocks]
        key = f"{self.doc.doc_id}|{','.join(block_ids)}|{piece.char_span}|{piece.split == 'table_rows'}"
        return Chunk(
            chunk_id=f"{self.doc.doc_id}::{pages[0]:03d}-{_sha(key)[:12]}",
            doc_id=self.doc.doc_id,
            ordinal=0,
            **meta,
            section_path=piece.node.path(),
            section_ids=list(dict.fromkeys(b.section_id for b in blocks if b.section_id)),
            clause_id=clause.number.label if clause else None,
            clause_ids=clause_ids,
            page_start=pages[0],
            page_end=pages[-1],
            pages=pages,
            source_block_ids=block_ids,
            source_line_ids=line_ids,
            char_span=piece.char_span if piece.text_override is not None and piece.split != "table_rows" else None,
            text=text,
            context_header=header,
            lead_in=lead,
            retrieval_text=retrieval,
            token_count=count_tokens(text),
            retrieval_token_count=count_tokens(retrieval),
            content_hash=_sha(text),
            tables=tables,
            split=piece.split,
            confidence=confidence,
            confidence_reasons=reasons,
        )

    def excluded_records(self, meta: dict) -> list[Chunk]:
        """Non-retrievable content, preserved as records (never linked into the chunk sequence)."""

        def reason(b: Block) -> str | None:
            if b.block_id in self.nav_ids:
                return "navigation_toc"
            for f in b.flags:
                if f.startswith("suspected_chrome"):
                    return "site_chrome:" + f.split(":", 1)[1]
                if f.startswith("exclude_from_retrieval"):
                    return f.split(":", 1)[1]
            return None

        groups: list[tuple[str, list[Block]]] = []
        for b in self.all_blocks:
            r = reason(b)
            if r is None:
                continue
            if groups and groups[-1][0] == r and groups[-1][1][-1].page == b.page and self._adjacent(groups[-1][1][-1], b):
                groups[-1][1].append(b)
            else:
                groups.append((r, [b]))
        out = []
        for r, blocks in groups:
            text = "\n".join(b.text for b in blocks)
            path = ingestion_section_path(self.doc, blocks[0].section_id)
            block_ids = [b.block_id for b in blocks]
            out.append(Chunk(
                chunk_id=f"{self.doc.doc_id}::x{blocks[0].page:03d}-{_sha(self.doc.doc_id + '|x|' + ','.join(block_ids))[:12]}",
                doc_id=self.doc.doc_id, ordinal=len(out), **meta,
                section_path=path, section_ids=list(dict.fromkeys(b.section_id for b in blocks if b.section_id)),
                clause_id=None, clause_ids=[], page_start=blocks[0].page, page_end=blocks[-1].page,
                pages=sorted({b.page for b in blocks}), source_block_ids=block_ids,
                source_line_ids=[lid for b in blocks for lid in b.line_ids],
                text=text, context_header="", retrieval_text="", token_count=count_tokens(text),
                retrieval_token_count=0, content_hash=_sha(text), split="excluded", retrievable=False,
                exclusion_reason=r, confidence="low" if any(b.confidence == "low" for b in blocks) else "high",
            ))
        return out

    def _adjacent(self, a: Block, b: Block) -> bool:
        return self.order[b.block_id] == self.order[a.block_id] + 1

    def furniture_reason(self, piece: Piece) -> str | None:
        """Chunks that are page furniture rather than content (kept as excluded records):
        * navigation_links: nothing but headings of empty sections and link tiles (items ingestion
          demoted from a run of same-style headings), e.g. Michigan's "Join our Travel Slack Channel",
          "Sign up for our Travel Program Newsletter", "Travel Resources: Online Booking | Group Travel";
        * document_metadata: a lone labeled date stamp already captured in the document metadata
          (Rochester "ISSUED ON 03/2026" -> issued_date 2026-03)."""
        blocks = self.text_blocks(piece)
        if not blocks or piece.text_override is not None:
            return None
        if all(b.kind == "heading" or "heading_run_demoted" in b.flags for b in blocks):
            return "navigation_links"
        if len(blocks) == 1 and self._is_date_stamp(blocks[0]):
            return "document_metadata"
        return None

    def _is_date_stamp(self, b: Block) -> bool:
        md = self.doc.metadata
        for lf in md.labeled_fields:
            key = DATE_LABELS.get(lf.label.lower().rstrip(":").strip())
            if key and lf.page == b.page and getattr(md, key).value == lf.value and norm(b.text) == norm(f"{lf.label} {lf.value}"):
                return True
        return False

    def as_excluded(self, c: Chunk, reason: str) -> Chunk:
        return c.model_copy(update={
            "chunk_id": c.chunk_id.replace("::", "::x", 1), "retrievable": False, "exclusion_reason": reason,
            "split": "excluded", "context_header": "", "lead_in": None, "retrieval_text": "", "retrieval_token_count": 0,
        })

    def run(self) -> DocResult:
        meta = self.metadata()
        chunks: list[Chunk] = []
        moved: list[Chunk] = []
        for p in self.chunk_node(self.tree.root):
            c = self.to_chunk(p, meta)
            if c is None:
                continue
            reason = self.furniture_reason(p)
            (moved if reason else chunks).append(self.as_excluded(c, reason) if reason else c)
        for i, c in enumerate(chunks):
            c.ordinal = i
            c.prev_chunk_id = chunks[i - 1].chunk_id if i else None
            c.next_chunk_id = chunks[i + 1].chunk_id if i + 1 < len(chunks) else None
        excluded = sorted(self.excluded_records(meta) + moved, key=lambda x: self.order[x.source_block_ids[0]])
        for i, x in enumerate(excluded):
            x.ordinal = i
        return DocResult(chunks, excluded, self.diagnostics(chunks, excluded))

    def diagnostics(self, chunks: list[Chunk], excluded: list[Chunk]) -> list[dict]:
        diag = list(self.tree.diagnostics)
        ids = [c.chunk_id for c in chunks + excluded]
        if len(ids) != len(set(ids)):
            diag.append({"code": "duplicate_chunk_id", "message": "chunk ids collide"})
        covered = {bid for c in chunks + excluded for bid in c.source_block_ids}
        heading_ids = {bid for bid, n in self.tree.node_of.items() if n.is_heading_like and n.block.block_id == bid}
        for b in self.blocks:
            if b.block_id not in heading_ids and b.block_id not in covered:
                diag.append({"code": "block_not_chunked", "block_id": b.block_id, "page": b.page,
                             "message": f"retrievable block not in any chunk: {b.text[:60]!r}"})
        used_paths = {n for c in chunks for n in c.section_path}
        for b in self.blocks:
            if b.block_id in heading_ids and b.block_id not in covered and b.text not in used_paths:
                diag.append({"code": "empty_section", "block_id": b.block_id, "page": b.page,
                             "message": f"heading with no retrievable content: {b.text[:60]!r}"})
        grid_pages = sorted({b.page for b in self.blocks if b.block_id in self.tree.grid_block_ids})
        if grid_pages:
            diag.append({"code": "unreconstructed_table_region", "message": f"pages {grid_pages}"})
        if self.nav_ids:
            pages = sorted({b.page for b in self.all_blocks if b.block_id in self.nav_ids})
            diag.append({"code": "navigation_excluded", "message": f"{len(self.nav_ids)} blocks on pages {pages}"})
        for c in chunks:
            if c.retrieval_token_count > C.MAX_TOKENS:
                diag.append({"code": "oversized_chunk", "chunk_id": c.chunk_id,
                             "message": f"{c.retrieval_token_count} > {C.MAX_TOKENS} tokens"})
        return diag


def chunk_document(doc: Document) -> DocResult:
    if doc.status != "ok":
        return DocResult([], [], [{"code": "document_failed", "message": "ingestion status is not ok"}])
    return DocChunker(doc).run()
