"""Ingestion layer: PDF discovery, extraction, cleaning, structure detection.

Pipeline (one document):

    discover -> extract -> clean -> layout (reading order) -> structure -> metadata

Every stage is conservative and reversible: extracted lines are never deleted,
only marked with a ``removed`` reason, and each page keeps PyMuPDF's untouched
``raw_text``. See docs/ingestion.md for heuristics and known limitations.
"""

PIPELINE_VERSION = "ingest-0.1.0"
