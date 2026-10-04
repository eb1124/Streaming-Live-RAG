"""Resolve the authored cases (cases.py) against the current chunks and freeze queries.jsonl.

  python -m evaluation.retrieval.build            write evaluation/retrieval/queries.jsonl
  python -m evaluation.retrieval.build --check    verify queries.jsonl against the current chunks
                                                  (exit 1 on drift); never rewrites anything

Resolution: each evidence quote must occur (whitespace-normalized) in exactly one retrievable chunk
of the named document; otherwise the build fails. Quotes that also occur in *other* documents are
reported for manual review: they are never added to the gold set automatically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from chunking.pipeline import load_chunks, load_manifest

from .cases import CASES, DOCS

HERE = Path(__file__).resolve().parent
QUERIES = HERE / "queries.jsonl"
BENCHMARK_VERSION = "retrieval-bench-1"
TYPES = {"direct_fact", "paraphrase", "conditional", "exception", "numeric_threshold", "terminology",
         "clause_specific", "cross_section", "version_sensitive"}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def resolve(chunks_by_doc: dict) -> tuple[list[dict], list[str], list[str]]:
    errors, notices, out = [], [], []
    all_chunks = [c for cs in chunks_by_doc.values() for c in cs]
    ids = [c["id"] for c in CASES]
    if len(ids) != len(set(ids)):
        errors.append("duplicate case ids")
    for case in CASES:
        if case["type"] not in TYPES or not set(case.get("secondary", [])) <= TYPES:
            errors.append(f"{case['id']}: unknown question type")
        units = []
        for u_idx, unit in enumerate(case["units"]):
            sources = []
            for s in unit:
                doc_id = DOCS[s["doc"]]
                q = norm(s["evidence"])
                hits = [c for c in chunks_by_doc[doc_id] if q in norm(c.text)]
                if len(hits) != 1:
                    errors.append(f"{case['id']} unit {u_idx}: evidence {s['evidence'][:60]!r} matches {len(hits)} chunks in {s['doc']}")
                    continue
                c = hits[0]
                if c.chunk_id not in {x["chunk_id"] for x in sources}:
                    sources.append({
                        "chunk_id": c.chunk_id, "doc_id": doc_id, "pages": c.pages, "section_path": c.section_path,
                        "clause_id": c.clause_id, "evidence": s["evidence"], "content_hash": c.content_hash,
                    })
                unit_docs = {DOCS[x["doc"]] for x in unit}  # other sources of this unit are intended
                others = [x for x in all_chunks if x.doc_id not in unit_docs and q in norm(x.text)]
                for x in others:
                    notices.append(f"{case['id']}: evidence also occurs in {x.chunk_id} (not gold; review if it should be)")
            units.append({"sources": sources})
        out.append({
            "id": case["id"],
            "query": case["query"],
            "question_type": case["type"],
            "secondary_types": case.get("secondary", []),
            "expected_doc_ids": sorted({s["doc_id"] for u in units for s in u["sources"]}),
            "expected_version": case.get("expected_version"),
            "units": units,
            "relevance": "all_units" if len(units) > 1 else "any_source",
            "notes": case.get("notes", ""),
            "review": case.get("review"),
        })
    return out, errors, notices


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    chunks = load_chunks()
    manifest = load_manifest()
    cases, errors, notices = resolve(chunks)
    for n in notices:
        print("REVIEW", n)
    if errors:
        for e in errors:
            print("ERROR", e)
        return 1
    header = {
        "benchmark_version": BENCHMARK_VERSION,
        "chunker_version": manifest["chunker_version"],
        "corpus_input_hashes": {d["doc_id"]: d["input_content_hash"] for d in manifest["documents"]},
    }
    lines = [json.dumps({"_header": header}, ensure_ascii=False)] + [json.dumps(c, ensure_ascii=False) for c in cases]
    content = "\n".join(lines) + "\n"
    if args.check:
        current = QUERIES.read_text(encoding="utf-8") if QUERIES.exists() else ""
        if current != content:
            print("DRIFT: queries.jsonl differs from what the current chunks produce. Inspect before rebuilding.")
            return 1
        print(f"OK: {len(cases)} cases match the current chunks")
        return 0
    QUERIES.write_text(content, encoding="utf-8")
    digest = hashlib.sha256(content.encode()).hexdigest()[:16]
    print(f"wrote {QUERIES} ({len(cases)} cases, sha256 {digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
