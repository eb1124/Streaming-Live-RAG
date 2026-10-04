"""Model-agnostic token estimate: one token per word and per punctuation mark.

Kept in one place so a real tokenizer can be swapped in once the embedding model is chosen.
Calibration on this corpus: cl100k_base = 1.04x this estimate (p5 0.99x, p95 1.10x).
"""

import re

_TOKEN = re.compile(r"\w+|[^\w\s]")


def count_tokens(text: str) -> int:
    return len(_TOKEN.findall(text))


def split_tokens(text: str, size: int) -> list[str]:
    """Last-resort split into windows of `size` tokens, cut at whitespace between words."""
    pieces, current, count = [], [], 0
    for word in text.split():
        n = count_tokens(word)
        if current and count + n > size:
            pieces.append(" ".join(current))
            current, count = [], 0
        current.append(word)
        count += n
    if current:
        pieces.append(" ".join(current))
    return pieces
