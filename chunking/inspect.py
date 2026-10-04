"""Inspect chunking output.

  python -m chunking.inspect                        corpus statistics
  python -m chunking.inspect DOC                    every chunk of one document (id, path, clause, pages, tokens, links, text)
  python -m chunking.inspect DOC --brief            one line per chunk
  python -m chunking.inspect DOC --excluded         the document's preserved non-retrievable records
  python -m chunking.inspect --chunk CHUNK_ID       one chunk in full, incl. retrieval text and provenance
  python -m chunking.inspect --find "per diem"      chunks whose text contains a phrase
  python -m chunking.inspect --low                  every low/medium-confidence chunk
  add --rebuild to re-run chunking first; --write-report to also save data/chunks/chunk_report.md

DOC is a doc_id prefix or a case-insensitive substring of the document title or doc_id.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import textwrap
from collections import Counter

from . import config as C
from .models import Chunk
from .pipeline import load_chunks, load_manifest, run

WIDTH = 110
TOKEN_BINS = [(0, 24), (25, 49), (50, 99), (100, 149), (150, 199), (200, 249), (250, 299), (300, 349), (350, 400), (401, 10**9)]


def _short(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _pages(c: Chunk) -> str:
    return f"p{c.page_start}" if c.page_start == c.page_end else f"p{c.page_start}-{c.page_end}"


def _q(values: list[int], q: float) -> int:
    values = sorted(values)
    return values[min(len(values) - 1, int(q * (len(values) - 1) + 0.5))] if values else 0


def stats_report(chunks: dict[str, list[Chunk]], excluded: dict[str, list[Chunk]], manifest: dict) -> str:
    out: list[str] = []
    allc = [c for cs in chunks.values() for c in cs]
    allx = [c for cs in excluded.values() for c in cs]
    tokens = [c.retrieval_token_count for c in allc]
    text_tokens = [c.token_count for c in allc]
    out += ["# Chunk statistics", "", f"Chunker {manifest['chunker_version']}; parameters: "
            + ", ".join(f"{k}={v}" for k, v in manifest["parameters"].items() if k != "token_estimate"), ""]
    out += [f"- **Retrievable chunks:** {len(allc)} from {len(chunks)} documents",
            f"- **Excluded records (preserved, not indexed):** {len(allx)}",
            f"- **Retrieval tokens** (header + lead-in + text): min {min(tokens)}, p10 {_q(tokens, .1)}, median {_q(tokens, .5)}, "
            f"p90 {_q(tokens, .9)}, max {max(tokens)}, mean {statistics.mean(tokens):.0f}",
            f"- **Text tokens** (chunk text only): min {min(text_tokens)}, median {_q(text_tokens, .5)}, "
            f"p90 {_q(text_tokens, .9)}, max {max(text_tokens)}", ""]

    out += ["## Chunks per document", "", "| doc_id | domain | chunks | excluded | median tok | max tok | pages covered | multi-page | low | medium |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for doc_id, cs in chunks.items():
        if not cs:
            out.append(f"| {doc_id} | — | 0 | {len(excluded.get(doc_id, []))} | | | | | | |")
            continue
        conf = Counter(c.confidence for c in cs)
        pages = sorted({p for c in cs for p in c.pages})
        out.append(f"| {doc_id} | {cs[0].domain} | {len(cs)} | {len(excluded.get(doc_id, []))} | "
                   f"{_q([c.retrieval_token_count for c in cs], .5)} | {max(c.retrieval_token_count for c in cs)} | "
                   f"{len(pages)} | {sum(1 for c in cs if c.page_end > c.page_start)} | {conf['low']} | {conf['medium']} |")
    out.append("")

    out += ["## Chunks per domain", ""]
    for dom, n in sorted(Counter(c.domain for c in allc).items(), key=lambda kv: -kv[1]):
        out.append(f"- {dom}: {n}")
    out.append("")

    out += ["## Token distribution (retrieval tokens)", "", "| range | chunks | |", "|---|---|---|"]
    for lo, hi in TOKEN_BINS:
        n = sum(1 for t in tokens if lo <= t <= hi)
        label = f"{lo}-{hi}" if hi < 10**9 else f">{C.MAX_TOKENS}"
        out.append(f"| {label} | {n} | {'#' * round(60 * n / max(len(tokens), 1))} |")
    out.append("")

    out += ["## Page distribution", "", "| pages spanned | chunks |", "|---|---|"]
    for span, n in sorted(Counter(c.page_end - c.page_start + 1 for c in allc).items()):
        out.append(f"| {span} | {n} |")
    out.append("")

    out += ["## How boundaries were chosen", ""]
    for split, n in Counter(c.split for c in allc).most_common():
        out.append(f"- {split}: {n}")
    with_lead = sum(1 for c in allc if c.lead_in)
    out += [f"- chunks carrying a lead-in (the only overlap): {with_lead}", ""]

    oversized = [c for c in allc if c.retrieval_token_count > C.MAX_TOKENS]
    out += [f"## Oversized chunks (> {C.MAX_TOKENS} retrieval tokens): {len(oversized)}", ""]
    out += [f"- {c.chunk_id} {c.retrieval_token_count} tok: {_short(c.text, 80)}" for c in oversized]
    out.append("")

    low = [c for c in allc if c.confidence != "high"]
    out += [f"## Low/medium-confidence chunks: {len(low)} "
            f"(low {sum(1 for c in low if c.confidence == 'low')}, medium {sum(1 for c in low if c.confidence == 'medium')})", ""]
    reasons = Counter(r for c in low for r in c.confidence_reasons)
    out += [f"- {r}: {n}" for r, n in reasons.most_common()]
    out.append("")

    out += ["## Excluded records", "", "| reason | records | tokens |", "|---|---|---|"]
    by_reason: dict[str, list[Chunk]] = {}
    for c in allx:
        by_reason.setdefault(c.exclusion_reason, []).append(c)
    for r, cs in sorted(by_reason.items()):
        out.append(f"| {r} | {len(cs)} | {sum(c.token_count for c in cs)} |")
    out.append("")

    out += ["## Structural diagnostics", ""]
    for d in manifest["documents"]:
        codes = Counter(x["code"] for x in d["diagnostics"])
        if codes:
            out.append(f"- **{d['doc_id']}**: " + ", ".join(f"{k}×{v}" for k, v in sorted(codes.items())))
        for x in d["diagnostics"]:
            if x["code"] not in ("empty_section",):
                where = f" p{x['page']}" if x.get("page") else ""
                out.append(f"  - {x['code']}{where}: {x['message']}")
    out.append("")

    dup = Counter(c.content_hash for c in allc)
    shared = [h for h, n in dup.items() if n > 1]
    docs_of = {h: sorted({c.doc_id for c in allc if c.content_hash == h}) for h in shared}
    within = [h for h in shared if len(docs_of[h]) < dup[h]]
    out += ["## Duplicate text", "",
            f"- identical chunk texts within one document: {len(within)}",
            f"- identical chunk texts shared across documents: {len(shared) - len(within)} "
            + "(" + "; ".join(f"{' & '.join(d[:28] for d in docs)}: {n}" for docs, n in
                               Counter(tuple(v) for h, v in docs_of.items() if h not in within).items()) + ")", ""]
    return "\n".join(out)


def show_chunk(c: Chunk, full: bool = True) -> None:
    print("=" * WIDTH)
    print(f"{c.chunk_id}   #{c.ordinal}   {_pages(c)}   {c.retrieval_token_count} tok (text {c.token_count})   "
          f"split={c.split}   confidence={c.confidence}{' ' + str(c.confidence_reasons) if c.confidence_reasons else ''}")
    print(f"  path    {' > '.join(c.section_path) or '(document root)'}")
    print(f"  clause  {c.clause_id or '-'}   clauses in text: {', '.join(c.clause_ids) or '-'}")
    print(f"  blocks  {', '.join(c.source_block_ids)}" + (f"   char_span={c.char_span}" if c.char_span else ""))
    if c.retrievable:
        print(f"  prev    {c.prev_chunk_id or '-'}\n  next    {c.next_chunk_id or '-'}")
    else:
        print(f"  EXCLUDED: {c.exclusion_reason}")
    if c.lead_in:
        print("  lead-in " + _short(c.lead_in, WIDTH - 10))
    body = c.text if full else _short(c.text, 400)
    print(textwrap.indent("\n".join(textwrap.fill(line, WIDTH - 4) if len(line) > WIDTH - 4 and "|" not in line else line
                                    for line in body.split("\n")), "    "))


def select(chunks: dict[str, list[Chunk]], key: str) -> str:
    key_l = key.lower()
    matches = [d for d, cs in chunks.items()
               if d.startswith(key_l) or key_l in d or (cs and cs[0].title and key_l in cs[0].title.lower())]
    if len(matches) != 1:
        raise SystemExit(f"'{key}' matches {len(matches)} documents: {', '.join(matches) or 'none'}")
    return matches[0]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("doc", nargs="?")
    parser.add_argument("--brief", action="store_true")
    parser.add_argument("--excluded", action="store_true")
    parser.add_argument("--chunk")
    parser.add_argument("--find")
    parser.add_argument("--low", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    if args.rebuild or not (C.CHUNKS_DIR / "_manifest.json").exists():
        run()
    chunks, excluded, manifest = load_chunks(), load_chunks(excluded=True), load_manifest()

    if args.chunk:
        found = [c for cs in list(chunks.values()) + list(excluded.values()) for c in cs if c.chunk_id.startswith(args.chunk)]
        if not found:
            raise SystemExit(f"no chunk {args.chunk!r}")
        for c in found:
            show_chunk(c)
            print("  --- retrieval_text ---")
            print(textwrap.indent(c.retrieval_text, "  | "))
            print(f"  metadata: org={c.organization!r} title={c.title!r} domain={c.domain} type={c.document_type} "
                  f"authority={c.authority_level} version={c.version} effective={c.effective_date} superseded={c.superseded_date} "
                  f"current={c.is_current} series={c.series_id} captured={c.capture_date}")
            print(f"  lines: {', '.join(c.source_line_ids)}")
            for t in c.tables:
                print(f"  table {t.block_id} confidence={t.confidence} notes={t.notes} rows={len(t.rows)}")
        return 0
    if args.find:
        needle = args.find.lower()
        hits = [c for cs in chunks.values() for c in cs if needle in c.text.lower()]
        for c in hits:
            show_chunk(c, full=False)
        print(f"{len(hits)} chunk(s)")
        return 0
    if args.low:
        for cs in chunks.values():
            for c in cs:
                if c.confidence != "high":
                    show_chunk(c, full=False)
        return 0
    if args.doc is None:
        report = stats_report(chunks, excluded, manifest)
        print(report)
        if args.write_report:
            (C.CHUNKS_DIR / "chunk_report.md").write_text(report + "\n", encoding="utf-8")
        return 0

    doc_id = select(chunks, args.doc)
    items = excluded.get(doc_id, []) if args.excluded else chunks[doc_id]
    if items:
        c0 = items[0]
        print(f"{doc_id}: {c0.title} ({c0.organization}) type={c0.document_type} effective={c0.effective_date} "
              f"current={c0.is_current} — {len(items)} {'excluded records' if args.excluded else 'chunks'}")
    for c in items:
        if args.brief:
            print(f"#{c.ordinal:<3} {_pages(c):8} {c.retrieval_token_count:>4}t {c.split[:9]:9} {c.confidence[:3]:3} "
                  f"{(c.clause_id or '-'):8} {_short(' > '.join(c.section_path[-2:]), 45):45} | {_short(c.text, 60)}")
        else:
            show_chunk(c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
