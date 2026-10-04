"""Dense retrieval baseline: embed chunks once, search by exact cosine similarity.

  query -> query embedding -> dot product with the normalized chunk matrix -> top-k chunks

The index is a float32 matrix (one L2-normalized row per retrievable chunk) saved as .npy next to a
JSON sidecar recording the model, its pinned revision, the prefixes used and the chunk ids and
content hashes it was built from. 466 chunks x 1024 dims is under 2 MB, so exact search is a single
matrix-vector product; no vector database is needed.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from chunking.models import Chunk
from ingestion.config import PROJECT_ROOT

from .embedders import CANDIDATES, EmbedderSpec

INDEX_DIR = PROJECT_ROOT / "data" / "index"
BATCH_SIZE = 16


@dataclass
class Result:
    rank: int
    chunk_id: str
    score: float
    doc_id: str
    title: str | None
    organization: str | None
    section_path: list[str]
    page_start: int
    page_end: int
    text: str


def resolved_revision(repo: str) -> str | None:
    """Commit hash of the locally cached snapshot that was actually loaded."""
    from huggingface_hub import try_to_load_from_cache

    path = try_to_load_from_cache(repo, "config.json")
    if isinstance(path, str) and "snapshots" in Path(path).parts:
        parts = Path(path).parts
        return parts[parts.index("snapshots") + 1]
    return None


def load_model(spec: EmbedderSpec):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(spec.repo, revision=spec.revision, device="cpu", trust_remote_code=spec.trust_remote_code)


def passage_text(chunk: Chunk, spec: EmbedderSpec) -> str:
    return spec.passage_prefix + chunk.retrieval_text


def query_text(query: str, spec: EmbedderSpec) -> str:
    return spec.query_prefix + query


def encode(model, texts: list[str]) -> np.ndarray:
    vecs = model.encode(texts, batch_size=BATCH_SIZE, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
    return np.asarray(vecs, dtype=np.float32)


class DenseIndex:
    def __init__(self, spec: EmbedderSpec, chunks: list[Chunk], matrix: np.ndarray, meta: dict):
        self.spec, self.chunks, self.matrix, self.meta = spec, chunks, matrix, meta

    @classmethod
    def build(cls, spec: EmbedderSpec, chunks: list[Chunk], model) -> "DenseIndex":
        start = time.perf_counter()
        matrix = encode(model, [passage_text(c, spec) for c in chunks])
        meta = {
            "model_key": spec.key, "repo": spec.repo, "revision": spec.revision or resolved_revision(spec.repo),
            "query_prefix": spec.query_prefix, "passage_prefix": spec.passage_prefix,
            "max_seq_length": model.max_seq_length, "dimension": int(matrix.shape[1]),
            "embedded_field": "retrieval_text", "normalized": True, "similarity": "cosine (dot product)",
            "chunk_ids": [c.chunk_id for c in chunks], "content_hashes": [c.content_hash for c in chunks],
            "build_seconds": round(time.perf_counter() - start, 3),
        }
        return cls(spec, chunks, matrix, meta)

    def save(self, directory: Path | None = None) -> Path:
        directory = directory or INDEX_DIR / self.spec.key
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / "embeddings.npy", self.matrix)
        (directory / "meta.json").write_text(json.dumps(self.meta, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return directory

    @classmethod
    def load(cls, key: str, chunks: list[Chunk], directory: Path | None = None) -> "DenseIndex":
        directory = directory or INDEX_DIR / key
        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        by_id = {c.chunk_id: c for c in chunks}
        stale = [cid for cid, h in zip(meta["chunk_ids"], meta["content_hashes"]) if cid not in by_id or by_id[cid].content_hash != h]
        if stale or len(by_id) != len(meta["chunk_ids"]):
            raise RuntimeError(f"index {key} is stale ({len(stale)} changed chunks); rebuild it")
        return cls(CANDIDATES[key], [by_id[cid] for cid in meta["chunk_ids"]], np.load(directory / "embeddings.npy"), meta)

    def search(self, query_vec: np.ndarray, k: int = 10) -> list[Result]:
        scores = self.matrix @ query_vec
        order = np.lexsort((np.arange(len(scores)), -scores))[:k]  # ties broken by index order: deterministic
        out = []
        for rank, i in enumerate(order, 1):
            c = self.chunks[i]
            out.append(Result(rank, c.chunk_id, float(scores[i]), c.doc_id, c.title, c.organization,
                              c.section_path, c.page_start, c.page_end, c.text))
        return out


class DenseRetriever:
    def __init__(self, index: DenseIndex, model):
        self.index, self.model = index, model

    def retrieve(self, query: str, k: int = 10) -> list[Result]:
        vec = encode(self.model, [query_text(query, self.index.spec)])[0]
        return self.index.search(vec, k)


def result_dict(r: Result) -> dict:
    return asdict(r)
