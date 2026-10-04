"""Filesystem locations. Everything generated lives under data/, never under corpus/."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = PROJECT_ROOT / "corpus"
OUTPUT_DIR = PROJECT_ROOT / "data" / "ingested"
OVERRIDES_FILE = PROJECT_ROOT / "config" / "document_overrides.toml"
