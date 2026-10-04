"""Inspect ingested documents.

  python -m ingestion.inspect                      overview of every document
  python -m ingestion.inspect DOC                  one document: metadata, stats, warnings, outline
  python -m ingestion.inspect DOC --page 3         blocks of page 3 in reading order
  python -m ingestion.inspect DOC --page 3 --lines every line on page 3, incl. removed ones + reason
  python -m ingestion.inspect DOC --page 3 --raw   PyMuPDF's untouched text for page 3
  python -m ingestion.inspect --find "per diem"    where a phrase occurs (doc / page / block / bbox)
  add --rebuild to re-run ingestion first

DOC is a doc_id prefix or any case-insensitive substring of the original filename.
"""

from __future__ import annotations

import argparse
import sys
import textwrap

from .config import CORPUS_DIR, OUTPUT_DIR, OVERRIDES_FILE
from .models import Document
from .pipeline import load_documents, run
from .structure import is_chrome, section_path

WIDTH = 110


def _short(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _field(f) -> str:
    if f.value is None:
        return "—  (unknown)"
    extra = f" → {f.normalized}" if f.normalized and f.normalized != f.value else ""
    page = f" p{f.page}" if f.page else ""
    return f"{f.value}{extra}   [{f.source}{page}]"


def _sample_pages(doc: Document) -> list[int]:
    with_content = [p.page_number for p in doc.pages if p.blocks]
    if not with_content:
        return []
    picks = {with_content[0], with_content[len(with_content) // 2], with_content[-1]}
    return sorted(picks)


def _page_sample(doc: Document, page_number: int, n_chars: int = 220) -> str:
    page = doc.pages[page_number - 1]
    blocks = [b for b in page.blocks if not is_chrome(b)] or page.blocks
    return _short(" ¶ ".join(b.text for b in blocks), n_chars)


def overview(docs: list[Document]) -> None:
    for i, d in enumerate(docs, 1):
        md, s = d.metadata, d.stats
        print("=" * WIDTH)
        print(f"[{i}/{len(docs)}] {d.doc_id}   status={d.status}   profile={d.profile}")
        print(f"  file         {d.source.original_filename}")
        if d.status != "ok":
            for w in d.warnings:
                print(f"  ERROR        {w.code}: {w.message}")
            continue
        print(f"  title        {_field(md.title)}")
        print(f"  organization {_field(md.organization)}")
        print(f"  domain       {_field(md.domain)}")
        print(f"  pages        {s['pages']} (empty after cleaning: {s['empty_pages']})")
        print(
            f"  structure    headings={s['headings']} paragraphs={s['paragraphs']} "
            f"lists={s['lists']} (items={s['list_items']}) tables={s['tables']} sections={s['sections']}"
        )
        removed = {k[len("removed_"):]: v for k, v in s.items() if k.startswith("removed_")}
        print(f"  removed      {removed or '-'}   chrome-flagged blocks={s['chrome_flagged_blocks']}")
        notable = [w for w in d.warnings if w.severity != "info"]
        infos = sorted({w.code for w in d.warnings if w.severity == "info"})
        for w in notable:
            where = f" p{w.page}" if w.page else ""
            print(f"  WARNING{where:5} {w.code}: {_short(w.message, 80)}")
        if infos:
            print(f"  info         {', '.join(infos)}")
        for pn in _sample_pages(d):
            print(f"  sample p{pn:<3} {_page_sample(d, pn)}")


def detail(doc: Document) -> None:
    md = doc.metadata
    print(f"{doc.doc_id}  ({doc.source.original_filename})")
    print(f"  status={doc.status} profile={doc.profile} pages={doc.pdf.page_count if doc.pdf else '-'}")
    print(f"  file_sha256={doc.source.file_sha256[:16]}…  content_hash={(doc.content_hash or '')[:16]}…")
    print("\nMetadata")
    for name in ("title", "organization", "domain", "source_url", "version", "effective_date", "revision_date", "captured_at"):
        print(f"  {name:15} {_field(getattr(md, name))}")
    if md.labeled_fields:
        print("  labeled fields: " + "; ".join(f"{lf.label}={lf.value!r} (p{lf.page})" for lf in md.labeled_fields))
    print("\nStats")
    print("  " + ", ".join(f"{k}={v}" for k, v in doc.stats.items()))
    print("\nWarnings")
    for w in doc.warnings:
        where = f" p{w.page}" if w.page else ""
        print(f"  [{w.severity}]{where} {w.code}: {w.message}")
    print("\nOutline (sections)")
    for sec in doc.sections:
        print(f"  {'  ' * (sec.level - 1)}L{sec.level} p{sec.page_start:<3} {_short(sec.title, 90)}")
    print("\nPages")
    for p in doc.pages:
        kinds = {}
        for b in p.blocks:
            kinds[b.kind] = kinds.get(b.kind, 0) + 1
        removed = sum(1 for ln in p.lines if ln.removed)
        print(f"  p{p.page_number:<3} blocks={kinds or '-'} removed_lines={removed}  {_short(_page_sample(doc, p.page_number, 70), 70) if p.blocks else ''}")


def page_view(doc: Document, page_number: int, show_lines: bool, show_raw: bool) -> None:
    page = doc.pages[page_number - 1]
    print(f"{doc.doc_id}  page {page_number}/{len(doc.pages)}  ({page.width}x{page.height}pt, images={page.image_count})")
    if show_raw:
        print("-" * WIDTH + "\nRAW (PyMuPDF get_text, untouched)\n" + "-" * WIDTH)
        print(page.raw_text)
        return
    if show_lines:
        for ln in page.lines:
            tag = f"REMOVED:{ln.removed}" if ln.removed else "kept"
            rep = f" repairs={ln.repairs}" if ln.repairs else ""
            print(f"  {ln.line_id:9} y={ln.bbox[1]:6.1f} x={ln.bbox[0]:5.1f} {ln.size:4.1f}pt b={ln.bold_ratio:.1f} {tag:32} {_short(ln.text or ln.raw_text, 70)!r}{rep}")
        return
    for b in page.blocks:
        label = b.kind
        if b.kind == "heading":
            label += f" L{b.heading_level}"
        if b.list_type:
            label += f" ({b.list_type})"
        flags = f" {b.flags}" if b.flags else ""
        conf = f" conf={b.confidence}" if b.confidence != "high" else ""
        path = " > ".join(section_path(doc, b.section_id))
        print(f"- {b.block_id} {label}{conf}{flags}  size={b.size} bbox={tuple(round(v) for v in b.bbox)}")
        print(f"    section: {_short(path, 100) or '-'}")
        body = b.text if b.kind == "table" else textwrap.fill(b.text, WIDTH - 4)
        print(textwrap.indent(body, "    "))


def find(docs: list[Document], phrase: str) -> None:
    needle = phrase.lower()
    hits = 0
    for d in docs:
        for b in d.iter_blocks():
            idx = b.text.lower().find(needle)
            if idx >= 0:
                hits += 1
                ctx = b.text[max(0, idx - 50): idx + len(phrase) + 50]
                print(f"{d.doc_id}  p{b.page} {b.block_id} {b.kind} bbox={tuple(round(v) for v in b.bbox)}\n    …{_short(ctx, 150)}…")
    print(f"{hits} hit(s)")


def select(docs: list[Document], key: str) -> Document:
    key_l = key.lower()
    matches = [d for d in docs if d.doc_id.startswith(key_l) or key_l in d.source.original_filename.lower()]
    if len(matches) != 1:
        names = ", ".join(d.doc_id for d in matches) or "none"
        raise SystemExit(f"'{key}' matches {len(matches)} documents: {names}")
    return matches[0]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("doc", nargs="?", help="doc_id prefix or filename substring")
    parser.add_argument("--page", type=int)
    parser.add_argument("--lines", action="store_true", help="show every line incl. removed ones")
    parser.add_argument("--raw", action="store_true", help="show PyMuPDF's untouched page text")
    parser.add_argument("--find", metavar="TEXT")
    parser.add_argument("--rebuild", action="store_true", help="re-run ingestion first")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    docs = [] if args.rebuild else (load_documents(OUTPUT_DIR) if OUTPUT_DIR.exists() else [])
    if not docs:
        docs = run(CORPUS_DIR, OUTPUT_DIR, OVERRIDES_FILE)

    if args.find:
        find(docs, args.find)
    elif args.doc is None:
        overview(docs)
    else:
        doc = select(docs, args.doc)
        if args.page:
            if not 1 <= args.page <= len(doc.pages):
                raise SystemExit(f"page must be 1..{len(doc.pages)}")
            page_view(doc, args.page, args.lines, args.raw)
        else:
            detail(doc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
