"""python -m ingestion  ->  ingest corpus/ into data/ingested/ and print a one-line summary per document."""

import argparse
import sys
from pathlib import Path

from .config import CORPUS_DIR, OUTPUT_DIR, OVERRIDES_FILE
from .pipeline import run
from .profiles import load_overrides


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=CORPUS_DIR)
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    docs = run(args.corpus, args.out, OVERRIDES_FILE)
    for d in docs:
        n_warn = len([w for w in d.warnings if w.severity != "info"])
        pages = d.pdf.page_count if d.pdf else "-"
        print(f"{d.status:6} {d.doc_id:58} pages={pages:<4} warnings={n_warn}")
    unmatched = [n for n in load_overrides(OVERRIDES_FILE) if n not in {d.source.original_filename for d in docs}]
    for name in unmatched:
        print(f"WARNING: override entry matches no file in corpus: {name!r}")
    print(f"\n{len(docs)} documents -> {args.out}")
    return 0 if all(d.status == "ok" for d in docs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
