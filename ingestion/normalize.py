"""Character-level text normalization. Pure functions; the raw text is always kept separately."""

from __future__ import annotations

import re

# Presentation-form ligatures -> letters.
LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",
    "ﬆ": "st",
}

# Word/Acrobat PDFMaker exports of Calibri map some ligature glyphs to wrong code points.
# Observed in this corpus (e.g. "ExecuƟve", "aŌer", "Harƞord", "permiƩed"). Only applied to Calibri
# spans (or fonts a document override declares to be Calibri), because these characters are
# legitimate letters in other contexts.
CALIBRI_BROKEN_LIGATURES = {
    "Ɵ": "ti",  # Ɵ
    "Ō": "ft",  # Ō
    "ƞ": "tf",  # ƞ
    "Ʃ": "tt",  # Ʃ (Microsoft Print to PDF; PDFMaker exports drop this glyph to a wide "t" instead)
}

SPACE_LIKE = re.compile(r"[  -   　\t]")
ZERO_WIDTH = re.compile(r"[​-‍⁠﻿­]")
PRIVATE_USE = re.compile(r"[-]")  # icon fonts (dashicons, FontAwesome, Segoe icons)
MULTISPACE = re.compile(r" {2,}")

BULLET_GLYPHS = "•◦▪▫●○■□‣⁃∙·–-*"


def repair_calibri(text: str) -> tuple[str, int]:
    count = 0
    for bad, good in CALIBRI_BROKEN_LIGATURES.items():
        n = text.count(bad)
        if n:
            text = text.replace(bad, good)
            count += n
    return text, count


def normalize_text(text: str) -> tuple[str, list[str]]:
    """Return (normalized_text, list_of_repairs). Never drops letters or digits."""
    repairs: list[str] = []
    for lig, rep in LIGATURES.items():
        if lig in text:
            repairs.append(f"ligature:{lig}->{rep}")
            text = text.replace(lig, rep)
    # Private-use glyphs are icons; keep common bullet PUA code points as a bullet.
    if PRIVATE_USE.search(text):
        text = text.replace("", "•").replace("", "•")
        stripped = PRIVATE_USE.sub("", text)
        if stripped != text:
            repairs.append("private_use_glyphs_removed")
            text = stripped
    text = ZERO_WIDTH.sub("", text)
    text = SPACE_LIKE.sub(" ", text)
    text = MULTISPACE.sub(" ", text).strip()
    return text, repairs
