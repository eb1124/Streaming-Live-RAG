"""Query refinement (AdaptiveRAG phase 6): deterministic, from the question's own words. No model.

  refinements(question, coverage, aliases) -> [(strategy, target, query), ...]   in the order they are tried

  organization_focus  one per organization the coverage found no relevant source for: the question with the other
                      named organizations' names removed and the missing organization's full name appended
                      ("... (University of Connecticut)"), so retrieval weighs that organization's evidence.
  keyword_focus       the question without question words, auxiliaries, articles, pronouns and similar function
                      words (dates, numbers, amounts, names and "current"-type words are kept), for a retrieval that
                      matches on the content words only.

The controller issues the first candidate that is new (not an earlier query, compared case- and space-insensitively)
and that keeps the question's temporal intent (temporal.intent.parse_query: same kind and dates), so a refinement
can never select other versions than the question did. With no such candidate the loop stops.
"""

from __future__ import annotations

import re

from temporal.intent import parse_query

from .state import KEYWORD_FOCUS, ORGANIZATION_FOCUS, Coverage

STOPWORDS = {
    "what", "when", "how", "who", "whom", "whose", "which", "where", "why", "whether", "is", "are", "was", "were", "be",
    "been", "being", "does", "do", "did", "can", "could", "must", "should", "will", "would", "may", "might", "shall",
    "has", "have", "had", "a", "an", "the", "of", "to", "in", "on", "at", "for", "by", "with", "from", "into", "about",
    "and", "or", "if", "then", "than", "that", "this", "these", "those", "it", "its", "they", "them", "their", "there",
    "i", "we", "you", "my", "our", "your", "any", "some", "such", "say", "says", "said", "tell", "me", "please", "also",
}
_EDGE = re.compile(r"^[\"'“”‘’(\[]+|[\"'“”‘’)\],.;:?!]+$")


def keywords(question: str) -> str:
    words = []
    for token in question.split():
        word = _EDGE.sub("", token)
        if word and word.lower() not in STOPWORDS:
            words.append(word)
    return " ".join(words)


def strip_organizations(text: str, names: list[str], aliases: dict[str, str]) -> str:
    """`text` without the given organizations' full names and aliases (with a possessive), tidied."""
    for org in names:
        forms = [org] + [a for a, o in aliases.items() if o == org]
        for form in sorted(forms, key=len, reverse=True):
            flags = re.I if form == org else 0
            text = re.sub(rf"(?<!\w){re.escape(form)}(?:['’]s)?(?!\w)", " ", text, flags=flags)
    text = re.sub(r"\b(?:at|of|for|from|by|under|and)\s*(?=[,?.;]|$)", " ", text, flags=re.I)
    text = re.sub(r"\s+([,.;:?)])", r"\1", " ".join(text.split()))
    return re.sub(r"^[,;\s]+|,\s*(?=,)", "", text).strip()


def refinements(question: str, coverage: Coverage, aliases: dict[str, str]) -> list[tuple[str, str | None, str]]:
    named = coverage.signals.get("named_organizations", [])
    out = []
    for org in coverage.signals.get("gaps", []):
        focused = strip_organizations(question, [o for o in named if o != org], aliases)
        out.append((ORGANIZATION_FOCUS, org, f"{focused.rstrip(' ?.!')} ({org})"))
    out.append((KEYWORD_FOCUS, None, keywords(question)))
    return out


def normalized(query: str) -> str:
    return " ".join(query.casefold().split())


def same_temporal_intent(a: str, b: str) -> bool:
    x, y = parse_query(a), parse_query(b)
    return x.kind == y.kind and [(d.year, d.month, d.day) for d in x.dates] == [(d.year, d.month, d.day) for d in y.dates]
