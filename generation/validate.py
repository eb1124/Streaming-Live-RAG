"""Deterministic verification of a model answer against the supplied sources.

An answer is accepted only if ALL of these hold (otherwise the caller abstains):
  * the output parses as the schema: status in {answered, insufficient_evidence}, claims well-formed;
  * answered => at least one claim; insufficient_evidence => no claims and nothing in not_in_sources;
  * every claim cites at least one source label, and every label exists in the context;
  * every claim has at least one quote with a passage of >= MIN_QUOTE_CHARS, and every quote occurs
    verbatim in the text of ONE source the claim cites. Comparison is on normalized text (see
    `normalize`): Unicode compatibility forms, hyphen/dash and quote-mark variants, whitespace, case, and
    list-marker glyphs (bullets, the Word sub-bullet "o") are ignored. A quote may skip text only at an
    explicit list marker or elision ("...", "…"): each passage must then occur in that source in order;
  * every number in the claim text (amounts, percentages, days, dates, clause numbers) occurs in the
    text of a source the claim cites (digits, or the number written as a word: "seven" = 7), in that
    source's displayed metadata (title, effective/superseded date, clause, section path), or in the
    question (the user's own value restated, e.g. "a $180,000 contract"); the last case is recorded in
    Verdict.notes;
  * versions (context.version_groups: several documents of one series in the context):
      - a claim that cites a version must have a quote found in that version;
      - a claim citing several versions of one series is accepted only if each of its quotes from that
        series occurs in every cited version (the rule is common to them), never as a merge;
      - a claim relying on a version while other versions of the series are in the context must name
        the version's effective date in its text, unless its quotes occur in every version in the context,
        or the QUESTION itself selected exactly that one version (question_versions, from temporal
        resolution: "UConn's February 2026 procedures", "effective July 1, 2026", "currently");
      - a version-specific claim must not name the effective date of a version it does not cite unless it
        also names its own ("Under the July 1, 2026 procedures ..." citing only February is rejected);
  * every calendar date written in a claim ("March 1, 2026", "July 2026", "2026-07-01") occurs in the
    question, in the text of a cited source, or in a cited source's effective/superseded date;
  * not_in_sources entries (parts of the question the sources do not answer) contain no numbers except
    those in the question or in the sources' metadata: they describe a gap, never a sourced fact;
  * named entities (ENTITY_GROUPS: things of one kind that must not be taken for one another, each with
    its aliases; e.g. the Travel Card and the PCard): a claim that names an entity is rejected when its
    evidence is about another entity of the same group and not about that one:
      - its supporting quotes name another entity of the group and none of them names the claimed one
        (a PCard sentence quoted for a Travel Card claim, also from a source that mentions both); or
      - its quotes name no entity of the group, and the cited sources they were found in (text and
        displayed metadata) name another entity of the group and not the claimed one.
    Evidence that names both is accepted when the claimed entity is named where the claim is supported;
    evidence that names no entity of the group ("the card") is not judged by this rule.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from . import config as C
from .context import Context, Source, version_groups

_WS = re.compile(r"\s+")
# applied after NFKC (which already maps U+2011 -> U+2010, NBSP -> space, "…" -> "...")
_PUNCT = str.maketrans({
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"', "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-", "﹣": "-", "－": "-",
    "­": None, "​": None, "‌": None, "‍": None, "⁠": None, "﻿": None,
})
_BULLETS = "•◦▪▫●○■□‣⁃∙"
_BULLET = re.compile(f"[{_BULLETS}]")
_SUB_BULLET_O = re.compile(r"(?<!\S)o(?!\S)")  # Word level-2 bullet glyph left in extracted text ("business-only o segment")
_SEPARATOR = re.compile(f"[{_BULLETS}]|\\.\\.\\.")  # explicit skips allowed inside one quote (after NFKC)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
_WORDS.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
               "hundred": 100})
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]


# Entities that are different things although their rules read alike. group -> entity name -> the aliases under
# which a claim or a source names it, as a pattern over normalized text (`normalize`: case, hyphen and whitespace
# variants are already folded). An alias belongs to one entity of its group only. To add a distinction, add an
# entry: the check below is the same for every group.
ENTITY_GROUPS: dict[str, dict[str, re.Pattern]] = {
    "card": {
        # Travel Card, University Travel Card, University(-)Issued Travel Card, employee travel card, TCard, T-Card
        "Travel Card": re.compile(r"\btravel cards?\b|\bt-?cards?\b"),
        # PCard, P-Card, Purchasing Card, Procurement Card
        "PCard": re.compile(r"\bp-?cards?\b|\b(?:purchasing|procurement) cards?\b"),
    },
}


def entities_named(norm_text: str) -> dict[str, set[str]]:
    """group -> the entities of ENTITY_GROUPS that normalized text names."""
    return {group: {name for name, alias in members.items() if alias.search(norm_text)}
            for group, members in ENTITY_GROUPS.items()}


def normalize(text: str) -> str:
    """Comparison form: never used for display. Drops only characters that carry no wording."""
    t = unicodedata.normalize("NFKC", text).translate(_PUNCT)
    t = _BULLET.sub(" ", t).casefold()
    t = _SUB_BULLET_O.sub(" ", t)
    return _WS.sub(" ", t).strip()


def quote_passages(quote: str) -> list[str]:
    """A quote split at explicit list markers / elisions, each passage normalized (empty ones dropped)."""
    parts = _SEPARATOR.split(unicodedata.normalize("NFKC", quote))
    return [p for p in (normalize(x) for x in parts) if p]


def occurs(passages: list[str], norm_text: str) -> bool:
    """All passages occur in the text, in order and without overlap."""
    pos = 0
    for p in passages:
        i = norm_text.find(p, pos)
        if i < 0:
            return False
        pos = i + len(p)
    return True


def numbers_in(text: str) -> set[str]:
    """Numbers as canonical strings: thousands separators removed, trailing .00 kept distinct only if non-zero."""
    out = set()
    for m in _NUMBER.findall(text):
        n = m.replace(",", "")
        n = n.rstrip(".")
        if re.fullmatch(r"\d+\.0+", n):
            n = n.split(".")[0]
        if n.isdigit():
            n = n.lstrip("0") or "0"
        out.add(n)
    return out


def number_words_in(text: str) -> set[str]:
    return {str(_WORDS[w]) for w in re.findall(r"[a-z]+", text.casefold()) if w in _WORDS}


def names_date(text: str, d: date) -> bool:
    """The text names this date: 2026-07-01, 7/1/2026, July 1, 2026, July 2026, July 1, 1 July (full or
    abbreviated month). A month name alone does not count ("may" is also a verb)."""
    t = unicodedata.normalize("NFKC", text).casefold()
    if d.isoformat() in t or re.search(rf"\b0?{d.month}/0?{d.day}/{d.year}\b", t):
        return True
    full = _MONTHS[d.month - 1]
    month = rf"\b(?:{full}|{full[:3]}\.?)"
    day = rf"0?{d.day}(?:st|nd|rd|th)?\b"
    return bool(re.search(rf"{month}\s+(?:{day},?\s*)?{d.year}\b", t) or re.search(rf"{month}\s+{day}", t)
                or re.search(rf"\b{day}\s+{full}\b", t))


_MONTH_RE = "|".join(f"{m}|{m[:3]}" for m in _MONTHS)


def dates_in(text: str) -> set[tuple[int, int, int | None]]:
    """Calendar dates as (year, month, day or None): 2026-07-01, 2026-07, 7/1/2026, July 1(st), 2026,
    1 July 2026, July 2026 (full or abbreviated month). A day and month without a year is not a date here."""
    t = unicodedata.normalize("NFKC", text).casefold()
    out: set[tuple[int, int, int | None]] = set()
    for y, m, d in re.findall(r"\b(\d{4})-(\d{2})(?:-(\d{2}))?\b", t):
        out.add((int(y), int(m), int(d) if d else None))
    for m, d, y in re.findall(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", t):
        out.add((int(y), int(m), int(d)))
    day_first = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_RE})\.?,?\s+(\d{{4}})\b")
    for d, mon, y in day_first.findall(t):
        out.add((int(y), _month(mon), int(d)))
    t = day_first.sub(" ", t)  # so "1 July 2026" is not also read as "July 2026"
    for mon, d, y in re.findall(rf"\b({_MONTH_RE})\.?\s+(?:(\d{{1,2}})(?:st|nd|rd|th)?,?\s+)?(\d{{4}})\b", t):
        out.add((int(y), _month(mon), int(d) if d else None))
    return {x for x in out if 1 <= x[1] <= 12}


def _month(name: str) -> int:
    return next(i for i, m in enumerate(_MONTHS, 1) if m.startswith(name[:3]))


def _date_supported(d: tuple, allowed: set[tuple]) -> bool:
    """Same year and month, and the same day unless either side gives none ("July 2026" ~ 2026-07-01)."""
    return any(a[:2] == d[:2] and (a[2] is None or d[2] is None or a[2] == d[2]) for a in allowed)


@dataclass
class Claim:
    text: str
    sources: list[str]
    quotes: list[str]


@dataclass
class Verdict:
    ok: bool
    status: str | None  # answered | insufficient_evidence | None (unparseable)
    claims: list[Claim] = field(default_factory=list)
    abstention_reason: str = ""
    problems: list[str] = field(default_factory=list)
    not_in_sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # accepted, but worth recording (e.g. numbers taken from the question)


def parse(raw: str) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        return None, f"output is not JSON: {e}"
    if not isinstance(data, dict):
        return None, "output is not a JSON object"
    return data, None


def verify(raw: str, ctx: Context, question: str = "", question_versions: dict[str, list[str]] | None = None) -> Verdict:
    """question_versions: series_id -> doc_ids that the question itself selected (temporal.resolve
    Resolution.selected). Only a single selected version relaxes the version-naming rule; None = none."""
    data, err = parse(raw)
    if err:
        return Verdict(False, None, problems=[err])
    status = data.get("status")
    raw_claims = data.get("claims")
    gaps = data.get("not_in_sources", [])  # absent in outputs of the grounded-qa-1 schema
    if (status not in ("answered", "insufficient_evidence") or not isinstance(raw_claims, list)
            or not isinstance(gaps, list) or not all(isinstance(g, str) for g in gaps)):
        return Verdict(False, None, problems=["output does not match the schema"])
    gaps = [g.strip() for g in gaps if g.strip()]
    claims, problems = [], []
    for i, rc in enumerate(raw_claims, 1):
        if not (isinstance(rc, dict) and isinstance(rc.get("text"), str) and isinstance(rc.get("sources"), list)
                and isinstance(rc.get("quotes"), list)):
            problems.append(f"claim {i}: malformed")
            continue
        claims.append(Claim(rc["text"].strip(), [str(s).strip() for s in rc["sources"]], [str(q) for q in rc["quotes"]]))
    verdict = Verdict(False, status, claims, str(data.get("abstention_reason") or ""), problems, gaps)
    if status == "insufficient_evidence":
        if claims:
            problems.append("insufficient_evidence with claims")
        if gaps:
            problems.append("insufficient_evidence with not_in_sources")
        verdict.ok = not problems
        return verdict
    if not claims:
        problems.append("answered without claims")
    sources = ctx.by_label()
    norm = {label: normalize(_source_text(sources, label)) for label in sources}
    groups = version_groups(ctx)
    series_of = {label: sid for sid, docs in groups.items() for labels in docs.values() for label in labels}
    question_numbers = numbers_in(question)
    question_dates = dates_in(question)
    for i, cl in enumerate(claims, 1):
        if not cl.text:
            problems.append(f"claim {i}: empty text")
        if not cl.sources:
            problems.append(f"claim {i}: no citation")
            continue
        unknown = [s for s in cl.sources if s not in sources]
        if unknown:
            problems.append(f"claim {i}: cites unknown source(s) {unknown}")
            continue
        cited = list(dict.fromkeys(cl.sources))
        good = []  # (quote, passages)
        for q in cl.quotes:
            passages = quote_passages(q)
            if passages and max(map(len, passages)) >= C.MIN_QUOTE_CHARS:
                good.append((q, passages))
        if not good:
            problems.append(f"claim {i}: no supporting quote of >= {C.MIN_QUOTE_CHARS} characters")
        found_in: list[tuple[str, list[str], set[str]]] = []  # (quote, passages, cited labels containing it)
        for q, passages in good:
            hits = {label for label in cited if occurs(passages, norm[label])}
            if not hits:
                problems.append(f"claim {i}: quote not found in cited source(s): {q[:80]!r}")
            found_in.append((q, passages, hits))
        problems += _version_problems(i, cl, cited, found_in, groups, series_of, sources, norm, question_versions or {})
        problems += _entity_problems(i, cl, found_in, sources, norm)
        # numbers may also come from the cited sources' displayed metadata (effective dates, clause,
        # section titles): e.g. "the February 2026 procedures". Never from chunk ids (hash digits).
        cited_text = " ".join(_source_text(sources, s) for s in cited)
        meta = " ".join(_source_metadata(sources, s) for s in cited)
        # month names are not numbers: a date is checked as a date, not as its digits
        allowed_dates = question_dates | dates_in(cited_text) | {
            d for s in cited for v in (sources[s].chunk.effective_date, sources[s].chunk.superseded_date) if v
            for d in dates_in(v)}
        invented = sorted(f"{y}-{m:02d}" + (f"-{d:02d}" if d else "") for y, m, d in dates_in(cl.text)
                          if not _date_supported((y, m, d), allowed_dates))
        if invented:
            problems.append(f"claim {i}: date(s) {invented} are neither in the question nor in the cited source(s)")
        allowed = numbers_in(cited_text) | number_words_in(cited_text) | numbers_in(meta)
        extra = numbers_in(cl.text) - allowed
        missing = sorted(extra - question_numbers)
        if missing:
            problems.append(f"claim {i}: number(s) {missing} not in cited source(s)")
        elif extra:
            verdict.notes.append(f"claim {i}: number(s) {sorted(extra)} restated from the question, not from a source")
    all_meta = numbers_in(" ".join(_source_metadata(sources, s) for s in sources))
    for j, g in enumerate(gaps, 1):
        stray = sorted(numbers_in(g) - question_numbers - all_meta)
        if stray:
            problems.append(f"not_in_sources {j}: number(s) {stray} are neither in the question nor in source metadata")
    verdict.ok = not problems
    return verdict


def _entity_problems(i: int, cl: Claim, found_in, sources: dict, norm: dict[str, str]) -> list[str]:
    """A claim that names an entity of ENTITY_GROUPS must not rest on evidence about another entity of its group.
    Judged where the claim is supported: its quotes that were found, then the sources they were found in."""
    claimed = entities_named(normalize(cl.text))
    if not any(claimed.values()):
        return []
    supported = [(passages, hits) for _, passages, hits in found_in if hits]
    in_quotes = entities_named(" ".join(p for passages, _ in supported for p in passages))
    labels = sorted({label for _, hits in supported for label in hits})
    in_sources = entities_named(" ".join(f"{norm[label]} {normalize(_source_metadata(sources, label))}" for label in labels))
    problems = []
    for group, names in claimed.items():
        for name in sorted(names):
            for where, found in (("supporting quote(s)", in_quotes[group]), (f"cited source(s) {labels}", in_sources[group])):
                others = sorted(found - {name})
                if name in found:
                    break  # named where the claim is supported
                if others:
                    problems.append(f"claim {i}: names the {name}, but its {where} name only the {', '.join(others)}: "
                                    f"evidence about a different {group} does not support it")
                    break
    return problems


def _version_problems(i: int, cl: Claim, cited: list[str], found_in, groups, series_of, sources, norm,
                      question_versions: dict[str, list[str]]) -> list[str]:
    """Rules for claims that rely on one of several versions of a document present in the context."""
    problems = []
    for sid in dict.fromkeys(series_of[label] for label in cited if label in series_of):
        sp: list[str] = []
        docs = groups[sid]  # doc_id -> labels in context, ordered by effective date
        doc_of = {label: doc for doc, labels in docs.items() for label in labels}
        cited_docs = list(dict.fromkeys(doc_of[label] for label in cited if label in doc_of))
        # quotes that support this series (found in at least one cited version)
        series_quotes = [(q, p, hits) for q, p, hits in found_in if any(doc_of.get(h) for h in hits)]
        for doc in cited_docs:
            if not any(doc_of.get(h) == doc for _, _, hits in series_quotes for h in hits):
                sp.append(f"claim {i}: cites {_version_name(sources, docs[doc][0])} but none of its quotes is from that version")

        def in_version(passages, doc):
            return any(occurs(passages, norm[label]) for label in docs[doc])

        if not sp and len(cited_docs) > 1:
            for q, p, _ in series_quotes:
                absent = [d for d in cited_docs if not in_version(p, d)]
                if absent:
                    sp.append(f"claim {i}: merges versions: quote {q[:60]!r} is not in "
                              f"{', '.join(_version_name(sources, docs[d][0]) for d in absent)}; "
                              "state each version's rule in its own claim")
        # quotes common to every version in the context: nothing version-specific is claimed
        if not sp and not all(in_version(p, d) for _, p, _ in series_quotes for d in docs):
            eff = {doc: _iso(sources[labels[0]].chunk.effective_date) for doc, labels in docs.items()}
            names_own = any(eff[d] and names_date(cl.text, eff[d]) for d in cited_docs)
            others = [d for d in docs if d not in cited_docs and eff[d] and names_date(cl.text, eff[d])]
            if others and not names_own:
                sp.append(f"claim {i}: names {_version_name(sources, docs[others[0]][0])} but cites only "
                          f"{', '.join(_version_name(sources, docs[d][0]) for d in cited_docs)}")
            # the question itself selected exactly this one version: it need not be repeated in the claim
            asked = question_versions.get(sid) or []
            if not sp and not (len(asked) == 1 and cited_docs == asked):
                for doc in cited_docs:
                    if eff[doc] is None or not names_date(cl.text, eff[doc]):
                        sp.append(f"claim {i}: relies on {_version_name(sources, docs[doc][0])} while other versions are "
                                  "in the sources; the claim must name that version's effective date")
        problems += sp
    return problems


def _iso(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _version_name(sources: dict[str, Source], label: str) -> str:
    c = sources[label].chunk
    return f"the version effective {c.effective_date or 'unknown'}" + (f" of {c.title}" if c.title else "")


def _source_metadata(sources: dict, label: str) -> str:
    c = sources[label].chunk
    return " ".join(str(x) for x in (c.title, c.effective_date, c.superseded_date, c.clause_id, *c.section_path) if x)


def _source_text(sources: dict, label: str) -> str:
    s = sources[label]
    while s.duplicate_of:  # an identical-text source quotes the text it stands for
        s = sources[s.duplicate_of]
    return s.text
