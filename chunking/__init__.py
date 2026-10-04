"""Chunking layer: normalized ingestion output (data/ingested/) -> retrieval chunks (data/chunks/).

Chunks follow document structure (sections, numbered clauses, paragraphs, lists, tables), not
fixed token windows. The size limit is a constraint applied only when a structural unit is too
big. See docs/chunking.md for the strategy, parameters and known limitations.

This layer only reads data/ingested/; it never modifies it.
"""

CHUNKER_VERSION = "chunk-0.1.0"
