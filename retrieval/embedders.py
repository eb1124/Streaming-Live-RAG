"""Candidate dense embedding models and how each must be called.

Prefixes follow each model card: several models are trained with a query instruction and/or a
passage prefix, and retrieval quality drops if they are omitted. `revision` pins the exact Hugging
Face commit that was evaluated (resolved on first download; see evaluation/retrieval/README.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EmbedderSpec:
    key: str  # short name used in file paths and reports
    repo: str
    revision: str | None
    query_prefix: str = ""
    passage_prefix: str = ""
    trust_remote_code: bool = False
    why: str = ""
    notes: list[str] = field(default_factory=list)


BGE_QUERY = "Represent this sentence for searching relevant passages: "

CANDIDATES: dict[str, EmbedderSpec] = {s.key: s for s in [
    EmbedderSpec(
        "minilm-l6", "sentence-transformers/all-MiniLM-L6-v2", "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
        why="Widely used 22M-parameter baseline; tells us how much a retrieval-trained model adds.",
        notes=["trained on sentence pairs with 128-token inputs; max_seq_length 256 word pieces"],
    ),
    EmbedderSpec(
        "bge-small", "BAAI/bge-small-en-v1.5", "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a", query_prefix=BGE_QUERY,
        why="Smallest retrieval-trained model in the set (33M); cheapest CPU option.",
    ),
    EmbedderSpec(
        "bge-base", "BAAI/bge-base-en-v1.5", "a5beb1e3e68b9ab74eb54cfd186867f64f240e1a", query_prefix=BGE_QUERY,
        why="Same family at 110M: isolates the effect of model size.",
    ),
    EmbedderSpec(
        "e5-base", "intfloat/e5-base-v2", "f52bf8ec8c7124536f0efb74aca902b2995e5bcd", query_prefix="query: ", passage_prefix="passage: ",
        why="110M, different training recipe (weakly supervised pairs + query/passage prefixes).",
    ),
    EmbedderSpec(
        "arctic-m", "Snowflake/snowflake-arctic-embed-m-v1.5", "e58a8f756156a1293d763f17e3aae643474e9b8a", query_prefix=BGE_QUERY,
        why="110M model trained specifically for retrieval; strong on retrieval benchmarks for its size.",
    ),
    EmbedderSpec(
        "nomic-v1.5", "nomic-ai/nomic-embed-text-v1.5", "e9b6763023c676ca8431644204f50c2b100d9aab",
        query_prefix="search_query: ", passage_prefix="search_document: ", trust_remote_code=True,
        why="137M with an 8192-token context: no chunk is ever truncated.",
        notes=["requires trust_remote_code: runs modeling code fetched from nomic-ai/nomic-bert-2048 "
               "(commit 7710840340a098cfb869c4f65e87cf2b1b70caca when evaluated; that code is not pinned by this "
               "model's revision) and einops"],
    ),
    EmbedderSpec(
        "mxbai-large", "mixedbread-ai/mxbai-embed-large-v1", "b33106f585b9ce46904ad7443a3b52b7a63e231c", query_prefix=BGE_QUERY,
        why="335M upper bound: is a 3x larger model worth its CPU cost on this corpus?",
    ),
]}
