"""Document profiles: the place for source-specific behaviour.

The general pipeline asks the profile for knobs instead of branching on file names.
A profile is picked from PDF producer/creator, and can be overridden per document in
config/document_overrides.toml (which can also add extra drop patterns).

Profiles present in this corpus:
  web_print     Chrome "Print to PDF" of a web page (producer "Skia/PDF", creator "Mozilla/...").
                Has a browser header (timestamp + tab title) and footer (URL + "n/N"),
                site navigation, cookie banners, icon-font glyphs, CSS bullets without glyphs.
  office_export Word -> PDF (Microsoft Word / Acrobat PDFMaker). Running headers/footers,
                real bullet glyphs, Calibri ligature mis-encodings.
  generic       Anything else.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .models import PdfInfo


@dataclass
class Profile:
    name: str
    browser_chrome: bool = False  # remove browser header/footer bands, read URL/timestamp from them
    flag_site_chrome: bool = False  # flag (not remove) navigation before the title / after the content
    drop_line_patterns: list[re.Pattern] = field(default_factory=list)
    calibri_fonts: tuple[str, ...] = ()  # anonymized font names that are really Calibri (ligature repair)


def detect_profile_name(pdf: PdfInfo) -> str:
    producer = (pdf.producer or "").lower()
    creator = (pdf.creator or "").lower()
    if producer.startswith("skia/pdf") and "mozilla" in creator:
        return "web_print"
    if "word" in creator or "pdfmaker" in creator or "word" in producer:
        return "office_export"
    return "generic"


def build_profile(name: str, override: dict | None = None) -> Profile:
    override = override or {}
    name = override.get("profile", name)
    profile = Profile(name=name)
    if name == "web_print":
        profile.browser_chrome = True
        profile.flag_site_chrome = True
    profile.drop_line_patterns = [re.compile(p) for p in override.get("drop_line_patterns", [])]
    profile.calibri_fonts = tuple(override.get("calibri_fonts", []))
    return profile


def load_overrides(path: Path) -> dict[str, dict]:
    """Per-document overrides keyed by original filename. Missing file -> no overrides."""
    if not path.exists():
        return {}
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    return data.get("documents", {})
