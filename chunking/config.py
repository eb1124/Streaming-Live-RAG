"""Paths and size parameters. See docs/chunking.md ("Size limits") for why these values.

Token counts are estimates from chunking.tokens.count_tokens (words and punctuation marks).
On this corpus cl100k (OpenAI) produces ~1.04x the estimate (p95 1.10x); BERT-style WordPiece
vocabularies typically run 10-20% above cl100k on this kind of text.
"""

from ingestion.config import OUTPUT_DIR as INGESTED_DIR, PROJECT_ROOT

CHUNKS_DIR = PROJECT_ROOT / "data" / "chunks"

# Hard ceiling for the text that will be embedded (context header + carried lead-in + chunk text).
# 400 estimated tokens stays under a 512-token encoder/reranker window with room for the query.
MAX_TOKENS = 400
# Size that grouping and fallback splitting aim for. Structural units between TARGET and MAX
# are kept whole; they are never padded or cut to reach it.
TARGET_TOKENS = 256
# Sibling clauses (or a section's intro text) below this size may be grouped with neighbours
# under the same parent, up to TARGET_TOKENS.
SMALL_TOKENS = 100
# A whole heading subsection below this size is too thin to stand alone (e.g. a heading with a
# single date line) and is grouped with an adjacent sibling.
TINY_TOKENS = 24
# A lead-in (clause head or a "...:" line) carried into split continuations, only if at most this long.
LEAD_IN_MAX_TOKENS = 80
