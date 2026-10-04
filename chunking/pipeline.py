"""Chunk every ingested document: data/ingested/*.json -> data/chunks/.

  data/chunks/<doc_id>.jsonl            retrievable chunks, in reading order (prev/next linked)
  data/chunks/excluded/<doc_id>.jsonl   preserved non-retrievable content (chrome, TOCs, curated exclusions)
  data/chunks/_manifest.json            parameters, input hashes, per-document counts and diagnostics

Output is deterministic: the same ingestion output produces byte-identical files.
data/ingested/ is only read.
"""

from __future__ import annotations

import json
from pathlib import Path

from ingestion.models import Document
from ingestion.pipeline import load_documents

from . import CHUNKER_VERSION
from . import config as C
from .chunker import DocResult, chunk_document
from .models import Chunk

PARAMETERS = {
    "max_tokens": C.MAX_TOKENS,
    "target_tokens": C.TARGET_TOKENS,
    "small_tokens": C.SMALL_TOKENS,
    "tiny_tokens": C.TINY_TOKENS,
    "lead_in_max_tokens": C.LEAD_IN_MAX_TOKENS,
    "token_estimate": "words + punctuation marks (chunking.tokens.count_tokens)",
}


def _write_jsonl(path: Path, chunks: list[Chunk]) -> None:
    path.write_text("".join(c.model_dump_json() + "\n" for c in chunks), encoding="utf-8")


def run(ingested_dir: Path = C.INGESTED_DIR, out_dir: Path = C.CHUNKS_DIR) -> dict[str, DocResult]:
    docs = load_documents(ingested_dir)
    results = {d.doc_id: chunk_document(d) for d in docs}

    (out_dir / "excluded").mkdir(parents=True, exist_ok=True)
    for stale in list(out_dir.glob("*.jsonl")) + list((out_dir / "excluded").glob("*.jsonl")):
        stale.unlink()
    for doc_id, res in results.items():
        _write_jsonl(out_dir / f"{doc_id}.jsonl", res.chunks)
        _write_jsonl(out_dir / "excluded" / f"{doc_id}.jsonl", res.excluded)
    manifest = {
        "chunker_version": CHUNKER_VERSION,
        "parameters": PARAMETERS,
        "documents": [manifest_entry(d, results[d.doc_id]) for d in docs],
    }
    (out_dir / "_manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return results


def manifest_entry(doc: Document, res: DocResult) -> dict:
    return {
        "doc_id": doc.doc_id,
        "input_content_hash": doc.content_hash,
        "input_pipeline_version": doc.pipeline_version,
        "chunks": len(res.chunks),
        "excluded_records": len(res.excluded),
        "diagnostics": res.diagnostics,
    }


def load_chunks(out_dir: Path = C.CHUNKS_DIR, excluded: bool = False) -> dict[str, list[Chunk]]:
    base = out_dir / "excluded" if excluded else out_dir
    return {
        p.stem: [Chunk.model_validate_json(line) for line in p.read_text(encoding="utf-8").splitlines() if line]
        for p in sorted(base.glob("*.jsonl"))
    }


def load_manifest(out_dir: Path = C.CHUNKS_DIR) -> dict:
    return json.loads((out_dir / "_manifest.json").read_text(encoding="utf-8"))
