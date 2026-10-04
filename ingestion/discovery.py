"""Find PDFs under the corpus root and assign stable document IDs."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

SLUG_MAX_LEN = 48


@dataclass(frozen=True)
class DiscoveredFile:
    doc_id: str
    path: Path
    relative_path: str  # POSIX, relative to corpus root
    original_filename: str


def slugify(text: str, max_len: int = SLUG_MAX_LEN) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0] or slug[:max_len]
    return slug or "document"


def make_doc_id(relative_path: str) -> str:
    """Readable slug of the filename + short hash of the relative path.

    Depends only on the corpus-relative path, so it is stable across runs and
    machines, and unaffected by content changes (those show up in file_sha256).
    The hash disambiguates files whose slugs collide after truncation.
    """
    stem = Path(relative_path).stem
    digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:6]
    return f"{slugify(stem)}-{digest}"


def discover_pdfs(corpus_dir: Path) -> list[DiscoveredFile]:
    """Recursively find PDFs (case-insensitive extension), sorted by relative path."""
    corpus_dir = Path(corpus_dir)
    if not corpus_dir.is_dir():
        raise FileNotFoundError(f"Corpus directory not found: {corpus_dir}")
    found = []
    for path in corpus_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".pdf":
            rel = path.relative_to(corpus_dir).as_posix()
            found.append(DiscoveredFile(make_doc_id(rel), path, rel, path.name))
    found.sort(key=lambda f: f.relative_path)
    ids = [f.doc_id for f in found]
    if len(ids) != len(set(ids)):  # practically impossible; fail loudly rather than overwrite output
        raise RuntimeError("doc_id collision in corpus")
    return found


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
