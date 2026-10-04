"""python -m chunking  ->  chunk data/ingested/ into data/chunks/ and print a one-line summary per document."""

import argparse
import sys
from pathlib import Path

from . import config as C
from .pipeline import run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ingested", type=Path, default=C.INGESTED_DIR)
    parser.add_argument("--out", type=Path, default=C.CHUNKS_DIR)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    results = run(args.ingested, args.out)
    total = 0
    for doc_id, res in results.items():
        codes = sorted({d["code"] for d in res.diagnostics})
        print(f"{doc_id:58} chunks={len(res.chunks):<4} excluded={len(res.excluded):<4} {', '.join(codes)}")
        total += len(res.chunks)
    print(f"\n{total} chunks from {len(results)} documents -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
