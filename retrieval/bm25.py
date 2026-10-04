"""BM25 lexical retrieval baseline (Okapi BM25), implemented here with no search engine.

Analyzer, chosen before evaluation (Lucene/Pyserini "English" recipe, not tuned on the benchmark):
  1. lowercase; join thousands separators ("$7,500" -> "7500", "25,000.00" -> "25000.00")
  2. tokens are runs of letters/digits; dotted numbers stay whole ("4.4.3.1", "5.1.1", "PR7.1" -> "pr7.1")
  3. drop English stopwords (Lucene's classic list)
  4. Snowball (Porter2) stemming of alphabetic tokens

Scoring: BM25 with k1 = 0.9, b = 0.4 (Pyserini's defaults) and the Lucene idf
log(1 + (N - df + 0.5) / (df + 0.5)), which is never negative.
Indexed field: `retrieval_text` (header + lead-in + text), the same text the dense index embeds.
Ties are broken by corpus order, so rankings are deterministic.
"""

from __future__ import annotations

import math
import re
from collections import Counter

import snowballstemmer

from chunking.models import Chunk

from .dense import Result

K1 = 0.9
B = 0.4

# Lucene's EnglishAnalyzer stopword set (ENGLISH_STOP_WORDS_SET).
STOPWORDS = frozenset(
    "a an and are as at be but by for if in into is it no not of on or such that the their then there these "
    "they this to was will with".split()
)
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
_TOKEN = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)*")
_stemmer = snowballstemmer.stemmer("english")


def analyze(text: str) -> list[str]:
    text = _THOUSANDS.sub("", text.lower())
    out = []
    for tok in _TOKEN.findall(text):
        tok = tok.strip(".")
        if not tok or tok in STOPWORDS:
            continue
        out.append(_stemmer.stemWord(tok) if tok.isalpha() else tok)
    return out


class BM25Index:
    def __init__(self, chunks: list[Chunk], k1: float = K1, b: float = B):
        self.chunks, self.k1, self.b = chunks, k1, b
        self.doc_tf = [Counter(analyze(c.retrieval_text)) for c in chunks]
        self.doc_len = [sum(tf.values()) for tf in self.doc_tf]
        self.avgdl = sum(self.doc_len) / len(self.doc_len)
        df = Counter(t for tf in self.doc_tf for t in tf)
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self.postings: dict[str, list[int]] = {}
        for i, tf in enumerate(self.doc_tf):
            for t in tf:
                self.postings.setdefault(t, []).append(i)

    def scores(self, query: str) -> list[float]:
        scores = [0.0] * len(self.chunks)
        for term, qtf in Counter(analyze(query)).items():
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i in self.postings[term]:
                tf = self.doc_tf[i][term]
                norm = self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl)
                scores[i] += qtf * idf * tf * (self.k1 + 1) / (tf + norm)
        return scores

    def ranked(self, query: str, k: int | None = None) -> list[tuple[int, float]]:
        """(chunk index, score) in rank order; zero-score chunks are not returned."""
        s = self.scores(query)
        order = sorted((i for i in range(len(s)) if s[i] > 0), key=lambda i: (-s[i], i))
        return [(i, s[i]) for i in order[:k]]

    def search(self, query: str, k: int = 10) -> list[Result]:
        out = []
        for rank, (i, score) in enumerate(self.ranked(query, k), 1):
            c = self.chunks[i]
            out.append(Result(rank, c.chunk_id, score, c.doc_id, c.title, c.organization,
                              c.section_path, c.page_start, c.page_end, c.text))
        return out
