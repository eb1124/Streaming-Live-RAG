"""Inspect retrieval (dense, BM25 or hybrid) for one query.

  python -m retrieval.search "When is a travel card suspended?" --model arctic-m -k 5
  python -m retrieval.search "..." --method bm25|dense|hybrid     (hybrid = arctic-m + BM25 via RRF)
  python -m retrieval.search "..." --model bge-base --build     (re)build the index first
  python -m retrieval.search "..." --json                        machine-readable output

Indexes live in data/index/<model>/ and are rebuilt with --build or when missing.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap

from chunking.pipeline import load_chunks

from .bm25 import BM25Index
from .hybrid import HybridRetriever
from .dense import INDEX_DIR, DenseIndex, DenseRetriever, load_model, result_dict
from .embedders import CANDIDATES


def all_chunks():
    return [c for cs in load_chunks().values() for c in cs]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query")
    parser.add_argument("--model", default="arctic-m", choices=sorted(CANDIDATES), help="dense model (dense and hybrid)")
    parser.add_argument("--method", default="dense", choices=["dense", "bm25", "hybrid"])
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    chunks = all_chunks()
    if args.method == "bm25":
        results = BM25Index(chunks).search(args.query, args.k)
    else:
        spec = CANDIDATES[args.model]
        model = load_model(spec)
        if args.build or not (INDEX_DIR / spec.key / "meta.json").exists():
            DenseIndex.build(spec, chunks, model).save()
        index = DenseIndex.load(spec.key, chunks)
        if args.method == "dense":
            results = DenseRetriever(index, model).retrieve(args.query, args.k)
        else:
            results = HybridRetriever(index, model, BM25Index(index.chunks)).retrieve(args.query, args.k)
    if args.json:
        print(json.dumps([result_dict(r) for r in results], indent=1, ensure_ascii=False))
        return 0
    for r in results:
        pages = f"p{r.page_start}" if r.page_start == r.page_end else f"p{r.page_start}-{r.page_end}"
        print(f"#{r.rank}  {r.score:.4f}  {r.chunk_id}  {pages}")
        print(f"    {r.title} ({r.organization})")
        print(f"    {' > '.join(r.section_path) or '(document root)'}")
        print(textwrap.indent(textwrap.fill(" ".join(r.text.split()), 104, max_lines=4), "    "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
