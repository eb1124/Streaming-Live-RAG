"""Corpus validation report.

  python -m ingestion.validate                 validate data/ingested against corpus/ + config/corpus_lock.json
  python -m ingestion.validate --rebuild       re-run ingestion first
  python -m ingestion.validate --update-lock   record the current corpus hashes as the expected state
                                               (do this only after a deliberate corpus change)

Writes data/validation_report.md and prints it. Exit code 1 if the corpus differs from the lock,
a document failed, or ingested output is stale.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .config import CORPUS_DIR, OUTPUT_DIR, OVERRIDES_FILE, PROJECT_ROOT
from .discovery import discover_pdfs, file_sha256
from .models import Document
from .pipeline import MANIFEST_FIELDS, load_documents, run
from .structure import is_chrome

LOCK_FILE = PROJECT_ROOT / "config" / "corpus_lock.json"
REPORT_FILE = PROJECT_ROOT / "data" / "validation_report.md"


def corpus_hashes(corpus_dir: Path) -> dict[str, str]:
    return {f.relative_path: file_sha256(f.path) for f in discover_pdfs(corpus_dir)}


def integrity(current: dict[str, str], lock: dict[str, str]) -> dict[str, list[str]]:
    return {
        "unchanged": sorted(k for k in current if lock.get(k) == current[k]),
        "modified": sorted(k for k in current if k in lock and lock[k] != current[k]),
        "added": sorted(k for k in current if k not in lock),
        "missing": sorted(k for k in lock if k not in current),
    }


def _cell(value) -> str:
    return "—" if value is None else str(value).replace("|", "\\|")


def build_report(docs: list[Document], check: dict[str, list[str]], stale: list[str]) -> str:
    out: list[str] = ["# Corpus validation report", ""]
    ok = not (check["modified"] or check["added"] or check["missing"] or stale)
    out += ["## Raw corpus integrity", ""]
    out.append(f"Compared {len(check['unchanged']) + len(check['modified']) + len(check['added'])} PDFs in corpus/ "
               f"against config/corpus_lock.json: **{'OK' if ok else 'MISMATCH'}**")
    for key in ("modified", "added", "missing"):
        for name in check[key]:
            out.append(f"- {key.upper()}: {name}")
    for name in stale:
        out.append(f"- STALE INGEST (file changed since ingestion): {name}")
    out.append("")

    out += ["## Documents", "", "| doc_id | pages | type | domain | organization | authority | status |", "|---|---|---|---|---|---|---|"]
    for d in docs:
        md = d.metadata
        out.append(f"| {d.doc_id} | {d.pdf.page_count if d.pdf else '—'} | {_cell(md.document_type.value)} | "
                   f"{_cell(md.domain.value)} | {_cell(md.organization.value)} | {_cell(md.authority_level.value)} | {d.status} |")
    out.append("")

    out += ["## Dates, versions, provenance", "",
            "| doc_id | title | version | issued | effective | revised | series | superseded | current | captured | source_url |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for d in docs:
        md = d.metadata
        def dt(f):
            return _cell(f.normalized or f.value)
        out.append(f"| {d.doc_id} | {_cell(md.title.value)} [{md.title.source}] | {_cell(md.version.value)} | {dt(md.issued_date)} | {dt(md.effective_date)} | "
                   f"{dt(md.revision_date)} | {_cell(md.series_id.value)} | {dt(md.superseded_date)} | {_cell(md.is_current.value)} | "
                   f"{dt(md.captured_at)} | {_cell(md.source_url.value)} |")
    out.append("")

    out += ["## Missing metadata (explicit nulls)", ""]
    for d in docs:
        missing = [f for f in MANIFEST_FIELDS if getattr(d.metadata, f).value is None]
        out.append(f"- **{d.doc_id}**: {', '.join(missing) or 'none'}")
    out.append("")

    out += ["## Qualified provenance (notes on curated or indirect values)", ""]
    for d in docs:
        for f in ("organization", "title"):
            field = getattr(d.metadata, f)
            if field.note:
                out.append(f"- **{d.doc_id}** {f} = {field.value!r} (evidence {field.evidence!r}): {field.note}")
    out.append("")

    out += ["## Warnings", ""]
    for d in docs:
        notable = [w for w in d.warnings if w.severity != "info"]
        infos = Counter(w.code for w in d.warnings if w.severity == "info")
        out.append(f"- **{d.doc_id}**: " + ("; ".join(f"{w.code}{f' p{w.page}' if w.page else ''}: {w.message}" for w in notable) or "no warnings")
                   + (f"  _(info: {', '.join(f'{k}×{v}' for k, v in sorted(infos.items()))})_" if infos else ""))
    out.append("")

    out += ["## Extraction failures", ""]
    failed = [d for d in docs if d.status != "ok"]
    page_errors = [(d.doc_id, w) for d in docs for w in d.warnings if w.severity == "error"]
    out.append("none" if not failed and not page_errors else "")
    for d in failed:
        out.append(f"- {d.doc_id}: " + "; ".join(w.message for w in d.warnings))
    for doc_id, w in page_errors:
        out.append(f"- {doc_id} p{w.page}: {w.code} {w.message}")
    out.append("")

    out += ["## Suspicious / questionable documents", ""]
    for d in docs:
        reasons = [w.message for w in d.warnings if w.code == "curator_flag"]
        reasons += [f"{w.code} p{w.page}" for w in d.warnings if w.code in ("empty_after_cleaning", "no_text_layer", "override_rejected")]
        if d.metadata.authority_level.value == "informational":
            reasons.append("informational document (not a policy instrument)")
        if any(getattr(d.metadata, f).note and "INDIRECT" in getattr(d.metadata, f).note for f in ("organization",)):
            reasons.append("organization identified only indirectly")
        if reasons:
            out.append(f"- **{d.doc_id}**: " + "; ".join(reasons))
    out.append("")

    out += ["## Structure detection", "",
            "| doc_id | headings | sections | list items (low-conf) | tables (rejected) | columnar pages | chrome-flagged | retrieval-excluded |",
            "|---|---|---|---|---|---|---|---|"]
    for d in docs:
        blocks = list(d.iter_blocks())
        low_lists = sum(1 for b in blocks if b.kind == "list_item" and b.confidence == "low")
        rejected = sum(1 for w in d.warnings if w.code == "table_rejected")
        columnar = len({w.page for w in d.warnings if w.code == "columnar_layout"})
        excluded = sum(1 for b in blocks if any(f.startswith("exclude_from_retrieval") for f in b.flags))
        s = d.stats
        out.append(f"| {d.doc_id} | {s.get('headings', 0)} | {s.get('sections', 0)} | {s.get('list_items', 0)} ({low_lists}) | "
                   f"{s.get('tables', 0)} ({rejected}) | {columnar} | {sum(1 for b in blocks if is_chrome(b))} | {excluded} |")
    out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--update-lock", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    current = corpus_hashes(CORPUS_DIR)
    if args.update_lock:
        LOCK_FILE.write_text(json.dumps(current, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {LOCK_FILE} ({len(current)} files)")
        return 0
    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8")) if LOCK_FILE.exists() else {}
    docs = run(CORPUS_DIR, OUTPUT_DIR, OVERRIDES_FILE) if args.rebuild or not OUTPUT_DIR.exists() else load_documents(OUTPUT_DIR)
    stale = [d.source.relative_path for d in docs if current.get(d.source.relative_path) != d.source.file_sha256]
    stale += [p for p in current if p not in {d.source.relative_path for d in docs}]
    check = integrity(current, lock)
    report = build_report(docs, check, stale)
    REPORT_FILE.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n(written to {REPORT_FILE})")
    bad = check["modified"] or check["added"] or check["missing"] or stale or any(d.status != "ok" for d in docs)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
