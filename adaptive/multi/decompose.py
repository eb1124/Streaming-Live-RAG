"""Multi-intent decomposition (AdaptiveRAG phase 2): deterministic pattern matching, no model.

  d = decompose(question, organizations)     # Decomposition(intents=[Intent, ...], multi, reason, flags)

A question has several intents when it asks two or three separate questions:
  * two question sentences                    "What is X? When does Y apply?"
  * coordinated interrogative clauses         "What is X, and what happens if Y?"   "... and when is Z?"
                                              "What is X; how is Y?"   serial: "What is X, how is Y, and when is Z?"
  * one question about a list of organizations' subjects (_list_items)
                                              "What are A's x, B's y, and C's z?"   "What is A's x and B's y?"
    Only a "What / Which is / are ..." question whose every list item starts with its own organization, each a
    different one, followed by its subject. Each item becomes the question "What are <item>?": the user's words.
  * institution-led clauses (_LED): a clause that opens with its own scope naming an institution
                                              "At UConn, what is X, and at Rutgers, what is Y?"
                                              "For UConn, what is X; for Rutgers, what is Y?"
                                              serial: "At A, what is X, at B, what is Y, and at C, what is Z?"
    The scope must name an organization (a corpus organization, an alias, or an institution-style name); each such
    clause keeps its own scope instead of the first clause's ("at Rutgers, what is Y?").
  * one question asked of a list of institutions (_distribute)
                                              "What are the Travel Card rules at UConn and Rutgers?"
                                              "At UConn, Rutgers and McGill, what are the travel advance rules?"
    Only when the list follows "at / for / in", holds nothing but two or more different institutions (every one the
    question names), and ends the question or its leading scope. Each intent is the question with the list replaced
    by one institution. A question that compares them ("the difference between ...", "the same ...", "both") is one need.
Never split:
  * temporal compare questions (two or more dates; temporal.intent "compare"): versions are the concern of
    temporal resolution and phase 1
  * sentences that are not questions ("Explain why." belongs to the question before it); non-question
    sentences before the first question are context shared by every intent
  * "and" between noun phrases or amounts: the word after "and" must start a question (or, in a list question,
    every item must start with a different organization: "tips and gratuities Rutgers will reimburse" is one need,
    and so is "UConn's and Rutgers' card rules", where the first item has no subject of its own)
  * clauses shorter than MIN_CLAUSE_WORDS words ("..., and when?")
  * clauses that refer back to the previous one ("..., and how often is that?"): not an independent need
  * more than MAX_INTENTS clauses (the question goes to phase 1 unchanged)

Each intent gets a retrieval query built only from the user's words: the context sentences, the sentence's
leading scope ("Under UConn's July 1, 2026 procedures,", "At Penn,") and the clause. A clause that names no
organization, while the other clauses name exactly one, gets that organization's full name in parentheses
(short names: config/organization_aliases.toml). Sub-queries drive retrieval only; the fused path shows the
model the original question.

Organization boundary: an intent whose own words name an institution ("Harvard University", "University of
Chicago", "Boston College") that is not an organization of the corpus, and name no corpus organization, records
that name in `outside_corpus`. The controller treats such an intent as unsupported: no other organization's
evidence may stand in for it, however high the reranker scores it. Only institution-style names are recognized
(... University / College / Institute, University / College / Institute of ...); a bare name outside the alias list
("Harvard") is not.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from temporal.intent import parse_query

ALIASES_PATH = Path(__file__).resolve().parents[2] / "config" / "organization_aliases.toml"
MAX_INTENTS = 3  # the context holds MAX_SOURCES = 6 sources: a fourth intent would get about one
MIN_CLAUSE_WORDS = 3

_WH = r"(?:what|when|how|who|whom|whose|which|where|why|whether)"
_AUX = r"(?:is|are|was|were|does|do|did|can|could|must|should|will|would|may|has|have)"
# split points inside one question sentence; the lookahead keeps the interrogative word in the new clause
_CUTS = [re.compile(rf"\s*[,;]?\s+and\s+(?={_WH}\b)", re.I),  # "X, and what ..." / "X and when ..."
         re.compile(rf",\s+and\s+(?={_AUX}\b)", re.I),  # "X, and is ..." (an auxiliary needs the comma)
         re.compile(rf";\s+(?={_WH}\b|{_AUX}\b)", re.I)]  # "X; how ..."
_SERIAL = re.compile(rf",\s+(?={_WH}\b)", re.I)  # "X, how Y, and when Z": only when the list ends with ", and <wh>"
_SERIAL_END = re.compile(rf",\s+and\s+{_WH}\b", re.I)
# an institution-led clause: its own scope, then the question ("at Rutgers, what ...", "for UConn, is ...")
_PREP = r"(?:at|for|in|under|within)"
_LED = rf"{_PREP}\s+(?P<scope>[^,;?]{{1,80}}),\s+(?:{_WH}|{_AUX})\b"
_LED_CUTS = [re.compile(rf"\s*[,;]?\s+and\s+(?={_LED})", re.I),  # "X, and at Rutgers, what ..."
             re.compile(rf";\s+(?={_LED})", re.I)]  # "X; at Rutgers, what ..."
_LED_SERIAL = re.compile(rf",\s+(?={_LED})", re.I)  # "X, at B, what Y, and at C, what Z": only with the ", and" end
_LED_SERIAL_END = re.compile(rf",\s+and\s+{_LED}", re.I)
# a list of institutions after one preposition: "at UConn and Rutgers", "for UConn, for Rutgers and for McGill"
_LIST_BEFORE = re.compile(r"(?:^|\s)(?:at|for|in)\s+(?:the\s+)?$", re.I)
_LIST_BETWEEN = re.compile(r"^(?:\s*,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+)(?:(?:at|for|in)\s+)?(?:the\s+)?$", re.I)
_LIST_AFTER = re.compile(r"^\s*(?:,|$)")
_COMPARES = re.compile(r"\b(?:between|differ\w*|compar\w*|versus|vs|same|both|either|than)\b", re.I)
_WH_WORD = re.compile(rf"\b{_WH}\b", re.I)
_LEADING_AUX = re.compile(rf"^\s*{_AUX}\b", re.I)
_SENTENCE = re.compile(r"(?<=\?)\s+|(?<=[a-z0-9)%]\.)\s+(?=[A-Z])")
_COMMA = re.compile(r",\s+")
# a later clause that refers back ("how often is that?", "what does that method involve?") is not independent
_REFERS_BACK = re.compile(r"\b(?:it|its|they|them|their|this|that|these|those|such)\b", re.I)
# an organization the alias list does not know ("at Harvard University", "Harvard's"): never overwrite it
_OTHER_ORG = re.compile(r"\b(?:at|of|from|by)\s+(?:the\s+)?[A-Z][\w&.'’-]*|\b[A-Z][a-z]+['’]s\b")
# an institution-style name: "Harvard University", "Boston College", "University of Chicago"
_NAME = r"[A-Z][\w&.'’-]*"
_INSTITUTION = re.compile(rf"\b(?:{_NAME}\s+)+(?:University|College|Institute)\b"
                          rf"|\b(?:University|College|Institute) of (?:the )?{_NAME}(?:\s+(?:at\s+)?{_NAME})*")
# capitalized words that start a sentence or clause, not a name ("Which University Travel Card ...")
_NOT_A_NAME = {"What", "When", "How", "Who", "Whom", "Whose", "Which", "Where", "Why", "Whether", "Is", "Are", "Was",
               "Were", "Does", "Do", "Did", "Can", "Could", "Must", "Should", "Will", "Would", "May", "Has", "Have",
               "The", "A", "An", "At", "Under", "According", "For", "In", "On", "Of", "From", "By", "If", "And", "Or"}
# a question that asks for a thing by name: "What are <noun phrase>", "Which is <noun phrase>", "what about <noun phrase>"
_LIST_HEAD = re.compile(r"^(?:what|which)\s+(?:is|are|was|were)\s+", re.I)
_ABOUT_HEAD = re.compile(r"^what\s+about\s+", re.I)
_LIST_SEP = re.compile(r",\s+(?:and\s+)?|\s+and\s+", re.I)  # between the items of a list
_THE = re.compile(r"^the\s+", re.I)
_POSSESSIVE = r"(?:['’]s?)?(?!\w)\s*"


@dataclass
class Intent:
    index: int
    text: str  # the clause as the user wrote it
    sub_query: str  # what retrieval sees for this intent
    organizations: list[str] = field(default_factory=list)  # organizations the intent's own words name
    carried: list[str] = field(default_factory=list)  # organizations added from the other clauses
    outside_corpus: list[str] = field(default_factory=list)  # institutions it names that the corpus does not hold
    topic: str = ""  # what a "What is / are / about ..." intent asks for, in the user's words, without the organization


@dataclass
class Decomposition:
    question: str
    intents: list[Intent]
    multi: bool
    reason: str
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def load_aliases(path: Path = ALIASES_PATH) -> dict[str, str]:
    with open(path, "rb") as f:
        return dict(tomllib.load(f)["aliases"])


def _is_question(text: str) -> bool:
    return bool(_WH_WORD.search(text) or _LEADING_AUX.match(text))


def _words(text: str) -> int:
    return len(text.split())


def _clauses(sentence: str, names_institution=None) -> list[str]:
    """One question sentence -> its interrogative clauses (without the final "?"). names_institution(text): whether
    a clause's leading scope names an institution; with it, institution-led clauses are cut as well (_LED)."""
    body = sentence.rstrip().rstrip("?").rstrip()
    cuts = [m for rx in _CUTS for m in rx.finditer(body)]
    if _SERIAL_END.search(body):
        cuts += list(_SERIAL.finditer(body))
    if names_institution is not None:
        led = [m for rx in _LED_CUTS for m in rx.finditer(body)]
        if _LED_SERIAL_END.search(body):
            led += list(_LED_SERIAL.finditer(body))
        cuts += [m for m in led if names_institution(m.group("scope"))]
    cuts.sort(key=lambda m: m.start())
    pieces, last = [], 0
    for i, m in enumerate(cuts):
        if m.start() < last:  # overlapping candidates
            continue
        before = body[last:m.start()]
        after = body[m.end():cuts[i + 1].start() if i + 1 < len(cuts) else len(body)]
        asks = _is_question(before) or _is_question(_scope(before)[1])  # "At UConn, is a card required": a question
        if (asks and _words(before) >= MIN_CLAUSE_WORDS and _words(after) >= MIN_CLAUSE_WORDS
                and not _REFERS_BACK.search(after)):
            pieces.append(before.strip())
            last = m.end()
    pieces.append(body[last:].strip())
    return pieces


def _scope(piece: str) -> tuple[str, str]:
    """Leading comma-terminated phrase without an interrogative ("At Penn, ", "Under ... 2026, ") and the rest."""
    scope = ""
    for m in _COMMA.finditer(piece):
        prefix = piece[:m.end()]
        if _is_question(prefix):
            break
        scope = prefix
    return scope, piece[len(scope):]


def mentions(text: str, organizations: set[str], aliases: dict[str, str]) -> list[str]:
    """Organizations named in `text` (full names case-insensitively, short names exactly), in first-seen order."""
    found: list[tuple[int, str]] = []
    for org in organizations:
        m = re.search(rf"(?<!\w){re.escape(org)}(?!\w)", text, re.I)
        if m:
            found.append((m.start(), org))
    for alias, org in aliases.items():
        m = re.search(rf"(?<!\w){re.escape(alias)}(?:['’]s)?(?!\w)", text)
        if m:
            found.append((m.start(), org))
    return list(dict.fromkeys(org for _, org in sorted(found)))


def outside_corpus(text: str, organizations: set[str], aliases: dict[str, str]) -> list[str]:
    """Institution-style names in `text` that are not a corpus organization (nor contain or are contained in one,
    or an alias: "UConn University", "the University of Texas at Austin" are known)."""
    known = [x.lower() for x in (*organizations, *aliases)]
    out = []
    for m in _INSTITUTION.finditer(text):
        words = m.group(0).split()
        while words and words[0] in _NOT_A_NAME:
            words.pop(0)
        name = " ".join(words)
        if len(words) < 2 or any(k in name.lower() or name.lower() in k for k in known):
            continue
        out.append(name)
    return list(dict.fromkeys(out))


def _led_by(item: str, organizations: set[str], aliases: dict[str, str]) -> tuple[str, str] | None:
    """A noun phrase that starts with an organization ("UConn's travel advance requirements", "the Rutgers University
    Travel Card rules", "Harvard University's bid rules") -> (the organization as written, the rest); else None."""
    text = _THE.sub("", item.strip())
    names = [(o, re.I) for o in organizations] + [(a, 0) for a in aliases]
    for name, flags in sorted(names, key=lambda n: -len(n[0])):  # "Rutgers University" before "Rutgers"
        m = re.match(re.escape(name) + _POSSESSIVE, text, flags)
        if m:
            return name, text[m.end():]
    m = _INSTITUTION.match(text)  # an institution the corpus does not hold: still the item's own organization
    if m:
        rest = re.match(_POSSESSIVE, text[m.end():])
        return m.group(0), text[m.end() + (rest.end() if rest else 0):]
    return None


def _named(text: str, organizations: set[str], aliases: dict[str, str]) -> list[str]:
    return mentions(text, organizations, aliases) or outside_corpus(text, organizations, aliases)


def _list_items(piece: str, organizations: set[str], aliases: dict[str, str]) -> list[str] | None:
    """One question about a list of organizations' subjects -> one question per item, else None.

    "What are UConn's travel advance requirements, Rutgers University's Travel Card rules, and McGill University's
    non-travel advance policy" -> ["What are UConn's travel advance requirements", "What are Rutgers University's
    Travel Card rules", "What are McGill University's non-travel advance policy"]. The head is repeated as written.

    Split only when every item starts with an organization, names no second one, has a subject after it, and the
    organizations all differ. A list part that names no organization belongs to the item before it ("Rutgers' approval
    and settlement rules"), so an ordinary question with "and" or commas in it stays one question."""
    head = _LIST_HEAD.match(piece)
    if not head:
        return None
    rest = piece[head.end():]
    spans, start = [], 0
    for m in _LIST_SEP.finditer(rest):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(rest)))
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and not _named(rest[s:e], organizations, aliases):
            merged[-1] = (merged[-1][0], e)  # a continuation of the previous item, with its separator
        else:
            merged.append((s, e))
    items = [rest[s:e].strip() for s, e in merged]
    if len(items) < 2:
        return None
    names = []
    for item in items:
        led = _led_by(item, organizations, aliases)
        named = _named(item, organizations, aliases)
        if led is None or not led[1].strip() or len(named) != 1:
            return None
        names.append(named[0])
    if len(set(names)) != len(names):
        return None
    return [head.group(0) + item for item in items]


def _institution_spans(text: str, organizations: set[str], aliases: dict[str, str]) -> list[tuple[int, int, str]]:
    """Where `text` names institutions: (start, end, name) in order, the name canonical for a corpus organization
    or alias, as written for an institution-style name outside the corpus. Longer names win ("Rutgers University")."""
    spans: list[tuple[int, int, str]] = []

    def add(start: int, end: int, name: str) -> None:
        if not any(s < end and start < e for s, e, _ in spans):
            spans.append((start, end, name))

    names = [(o, re.I, o) for o in organizations] + [(a, 0, org) for a, org in aliases.items()]
    for name, flags, canonical in sorted(names, key=lambda n: -len(n[0])):
        for m in re.finditer(rf"(?<!\w){re.escape(name)}(?!\w)", text, flags):
            add(m.start(), m.end(), canonical)
    for m in _INSTITUTION.finditer(text):
        start = m.start()
        words = m.group(0).split()
        while words and words[0] in _NOT_A_NAME:  # "At Harvard University": the name starts after "At"
            start += len(words.pop(0)) + 1
        if len(words) >= 2:
            add(start, m.end(), " ".join(words))
    return sorted(spans)


def _distribute(text: str, organizations: set[str], aliases: dict[str, str]) -> list[str] | None:
    """One question asked of a list of institutions -> the question once per institution, else None.

    "what are the Travel Card rules at UConn and Rutgers" -> ["what are the Travel Card rules at UConn", "what are
    the Travel Card rules at Rutgers"]; "At UConn, Rutgers and McGill, " -> ["At UConn, ", "At Rutgers, ", "At McGill, "].
    The list must follow "at / for / in", hold only institutions (two or more, all different, and every institution
    the text names), joined by commas, "and" or "or", and be followed by a comma or by nothing. A text that
    compares them is not split."""
    if _COMPARES.search(text):
        return None
    spans = _institution_spans(text, organizations, aliases)
    if len(spans) < 2 or len({name for *_, name in spans}) != len(spans):
        return None
    if not _LIST_BEFORE.search(text[:spans[0][0]]) or not _LIST_AFTER.match(text[spans[-1][1]:]):
        return None
    if not all(_LIST_BETWEEN.match(text[a[1]:b[0]]) for a, b in zip(spans, spans[1:])):
        return None
    head, tail = text[:spans[0][0]], text[spans[-1][1]:]
    return [head + text[s:e] + tail for s, e, _ in spans]


def topic_of(text: str, organizations: set[str], aliases: dict[str, str]) -> str:
    """What a "What is / are ..." or "what about ..." question asks for, without the organization that leads it:
    "What are the UConn travel advance requirements?" -> "travel advance requirements". "" for any other question
    form (nothing is guessed), and for a phrase that names several organizations or holds another question."""
    body = text.strip().rstrip("?").strip()
    head = _LIST_HEAD.match(body) or _ABOUT_HEAD.match(body)
    if not head or "?" in body:
        return ""
    phrase = body[head.end():]
    if len(_named(phrase, organizations, aliases)) > 1 or _WH_WORD.search(phrase):
        return ""
    led = _led_by(phrase, organizations, aliases)
    if led:
        return led[1].strip()
    spans = _institution_spans(phrase, organizations, aliases)  # "... rules at UConn": the institution closes it
    if spans and spans[-1][1] == len(phrase) and _LIST_BEFORE.search(phrase[:spans[-1][0]]):
        phrase = _LIST_BEFORE.sub("", phrase[:spans[-1][0]])
    return _THE.sub("", phrase).strip()


def decompose(question: str, organizations: set[str] | None = None, aliases: dict[str, str] | None = None
              ) -> Decomposition:
    aliases = load_aliases() if aliases is None else aliases
    organizations = set(aliases.values()) if organizations is None else set(organizations)
    q = " ".join(question.split())

    def single(reason: str, flags: list[str] | None = None) -> Decomposition:
        return Decomposition(question, [Intent(0, q, question, mentions(q, organizations, aliases),
                                               topic=topic_of(_scope(q)[1], organizations, aliases))], False, reason,
                             flags or [])

    if parse_query(q).kind == "compare":
        return single("temporal compare question (versions are handled by temporal resolution and phase 1)")
    sentences = [s.strip() for s in _SENTENCE.split(q) if s.strip()]
    asks = [i for i, s in enumerate(sentences) if s.endswith("?")]
    if not asks:
        return single("no question sentence")
    context = " ".join(sentences[:asks[0]])
    clauses: list[tuple[str, str]] = []  # (scope, clause)
    listed = 0  # clauses that are items of a list question
    spread = 0  # clauses that are one question asked of a list of institutions

    def institution(text: str) -> bool:
        return bool(_named(text, organizations, aliases))

    for i in asks:
        pieces = _clauses(sentences[i], institution)
        if clauses and (_words(pieces[0]) < MIN_CLAUSE_WORDS or _REFERS_BACK.search(pieces[0])):
            scope, text = clauses[-1]  # "...? Why?" / "...? When does it apply?": part of the previous intent
            clauses[-1] = (scope, f"{text}? {sentences[i].rstrip('?')}")
            continue
        scope, first = _scope(pieces[0])
        items = _list_items(first, organizations, aliases) if len(pieces) == 1 else None
        if items:  # one question about a list of organizations' subjects: one clause per item
            listed += len(items)
            clauses += [(scope, item) for item in items]
            continue
        if len(pieces) == 1:  # one question asked of a list of institutions: once per institution
            scopes = _distribute(scope, organizations, aliases) if scope else None
            firsts = None if scopes else _distribute(first, organizations, aliases)
            if scopes or firsts:
                spread += len(scopes or firsts)
                clauses += [(s, first) for s in scopes] if scopes else [(scope, f) for f in firsts]
                continue
        clauses.append((scope, first))
        for p in pieces[1:]:
            own_scope, rest = _scope(p)
            if own_scope and institution(own_scope):  # an institution-led clause brings its own scope; a scope
                # without an institution before it ("Under the July 2026 procedures, ") still applies to it
                clauses.append((own_scope if institution(scope) else scope + own_scope, rest))
            else:
                clauses.append((scope, p))
    if len(clauses) < 2:
        return single("one question")
    if len(clauses) > MAX_INTENTS:
        return single(f"{len(clauses)} question clauses (more than MAX_INTENTS = {MAX_INTENTS})", ["too_many_intents"])

    bases = [" ".join(x for x in (context, f"{scope}{text}?") if x) for scope, text in clauses]
    own = [mentions(b, organizations, aliases) for b in bases]
    intents, flags = [], []
    for k, ((scope, text), base) in enumerate(zip(clauses, bases)):
        carried: list[str] = []
        outside = [] if own[k] else outside_corpus(base, organizations, aliases)
        if not own[k] and not outside and not _OTHER_ORG.search(f"{scope}{text}"):
            others = list(dict.fromkeys(o for j, orgs in enumerate(own) if j != k for o in orgs))
            if len(others) == 1:
                carried = others
            elif others:
                flags.append(f"ambiguous_organization:intent_{k}")
        sub = base + (f" ({'; '.join(carried)})" if carried else "")
        intents.append(Intent(k, f"{text}?", sub, own[k], carried, outside, topic_of(text, organizations, aliases)))
    reason = f"{len(intents)} question clauses"
    if listed:
        reason += f" ({listed} from one question about a list of organizations' subjects)"
    if spread:
        reason += f" ({spread} from one question asked of a list of institutions)"
    return Decomposition(question, intents, True, reason, flags)
