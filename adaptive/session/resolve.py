"""Follow-up resolution (AdaptiveRAG phase 3): deterministic pattern matching against the session, no model.

  resolution = resolve(question, session.last, organizations, aliases)

  self_contained  the first turn, or a question that names its own organization and does not refer back:
                  the question goes to the multi-intent controller verbatim (exactly phase 2)
  follow_up       a question that refers back ("Does that apply currently?", "this policy", "What about ...?")
                  or names no organization ("What documentation is required after the trip?"): it is rewritten
                  into one standalone query, a context sentence with the earlier question(s) and then the
                  question itself:
                    "(Earlier in this conversation: What is UConn's University Travel Card suspension rule).
                     Does that apply currently?"
                  Phase 2 treats a non-question sentence before the question as context shared by every intent,
                  so its decomposition, retrieval, temporal resolution and verification run unchanged on it.
  refinement      a late constraint on the question before it: not a question, but a correction ("Actually, the
                  trip was international."), a statement about what was asked ("This was for international
                  travel.") or an instruction ("Use the July 2026 version instead."), about the same organization,
                  that adds at least one new content word (the delta). The question stays the one that was asked,
                  with the constraints as its context: "(Earlier in this conversation: the trip was
                  international). What are the travel reimbursement rules for an employee trip?". What is retrieved
                  is the delta with the topic it applies to (signals.retrieval_query: "international travel
                  reimbursement rules for an employee trip"), and the earlier answer is refined, not redone
                  (adaptive.session.refine). The rules that make a follow-up unresolved apply to it as well.
  unresolved      the reference cannot be resolved safely: no retrieval, no model call, an explicit abstention
                  * the previous turn asked several questions (which one does "that" mean?)
                  * the topic names several organizations
                  * the question refers back but names another organization ("Does Rutgers have the same rule?")
                  * the previous turn was itself unresolved

What a follow-up is read with (Resolution.topic): the question that started the topic and, when the previous turn
was a follow-up, that question too; never the previous answer. Answer text in the query would let the verifier
accept its numbers as the user's own (numbers named in the question need no source), so earlier model output
never becomes evidence. The earlier questions are shown without their dates or "current": the temporal
constraint in effect is the follow-up's own when it names one ("currently", "in February 2026"), else the one
inherited from the topic, stated once ("; version: February 2026"). Temporal resolution then selects versions from the rewritten query as for
any question.
"""

from __future__ import annotations

import re

from adaptive.multi.decompose import mentions, outside_corpus
from temporal.intent import MONTHS, parse_query

from .state import FOLLOW_UP, REFINEMENT, SELF_CONTAINED, UNRESOLVED, Resolution, Turn

_AUX = r"(?:does|do|did|is|are|was|were|would|will|can|could|should|must|has|have)"
# a question opening on a reference to what came before: "Does that apply ...?", "And is it ...?"
_REF_OPENER = re.compile(rf"^\s*(?:(?:and|but|so)\s+)?{_AUX}\s+(?:that|this|it|these|those|they|the same)\b", re.I)
# a noun phrase pointing back: "this policy", "that rule", "the same limit"
_REF_PHRASE = re.compile(r"\b(?:that|this|these|those|the same|such an?|such)\s+(?:rules?|polic(?:y|ies)|procedures?|"
                         r"requirements?|limits?|deadlines?|documents?|versions?|provisions?|process|amount|answer|one)\b",
                         re.I)
# a question continuing the previous one: "What about ...?", "And when ...?", "What if ...?"
_CONTINUATION = re.compile(r"^\s*(?:and|also|so|then|what about|how about|same for|what if)\b", re.I)
# a name the organization lists do not know: "at Harvard", "Harvard's" (only when no known organization is named)
_NAMED = re.compile(r"\b(?:at|of|from|by)\s+(?:the\s+)?([A-Z][\w&.-]*)|\b([A-Z][a-z]+)['’]s\b")
_NOT_A_NAME = {"What", "Who", "How", "Where", "When", "Why", "Which", "That", "This", "It", "There", "Here", "The",
               "A", "An", "I"}
# a date is removed together with the word that introduces it ("effective July 1, 2026"); "current" alone
_LEAD = r"(?:\b(?:effective|as of|dated|on|in|from|since|under|for)\s+(?:the\s+)?)?"
# ---- a late constraint (see late_constraint)
ASKED = (SELF_CONTAINED, FOLLOW_UP, REFINEMENT)  # turns that asked the corpus something: what can be refined
_INTERROGATIVE = re.compile(rf"^\s*(?:(?:and|but|so)\s+)?(?:what|which|who|whom|whose|when|where|why|how|{_AUX})\b", re.I)
# words that announce a correction; they are not part of the constraint
_CORRECTION = re.compile(r"^\s*(?:actually|sorry|correction|to clarify|to be clear|to be specific|more precisely|"
                         r"in fact|rather|oh|no|i meant|i mean|i should have said|i should add|make that|note that)"
                         r"\b[\s,:;.\-–—]*", re.I)
# a statement about what was asked: "The trip was international", "This is for domestic travel", "It was in 2026"
_STATEMENT = re.compile(r"^(?:the|this|that|it|they|he|she|we|i|my|our|these|those)\b[^.?!]*?\b(?:was|is|were|are|am|"
                        r"will be|has been|had|has|have)\b", re.I)
# an instruction on how to read the question: "Use the July 2026 version", "For international travel"
_INSTRUCTION = re.compile(r"^(?:use|apply|assume|consider|for|under|as of)\b", re.I)
_INSTEAD = re.compile(r"[\s,]+(?:instead|only|please)\b\.?\s*$", re.I)
_ASKS_FOR = re.compile(r"^(?:(?:what|which)\s+(?:is|are|was|were)|what\s+about|how\s+about)\s+(?:the\s+)?", re.I)
_TOKEN = re.compile(r"[A-Za-z0-9][\w'’-]*")
_FUNCTION_WORDS = set(
    "a an the this that these those it its they them their he she his her we our i my me you your of for to in on at "
    "by with from as about into under over and or but nor so if then than is are was were am be been being has have "
    "had do does did will would can could should may might must only just also use using used apply assume "
    "consider take make made mean meant actually sorry instead please version one".split())


def references(question: str) -> list[str]:
    """The words by which a question refers to the conversation (empty: it does not)."""
    return [m.group(0).strip() for rx in (_REF_OPENER, _REF_PHRASE, _CONTINUATION) for m in rx.finditer(question)]


def unknown_names(question: str, topic: str = "") -> list[str]:
    """Capitalized names after at/of/from/by, or possessives, that are neither months nor words of the topic."""
    known = set(re.findall(r"[\w&.-]+", topic))
    out = []
    for m in _NAMED.finditer(question):
        name = m.group(1) or m.group(2)
        if name.lower() in MONTHS or name in _NOT_A_NAME or name in known:
            continue
        out.append(name)
    return list(dict.fromkeys(out))


def strip_temporal(text: str) -> str:
    """`text` without its dates and "current"-type words, so that it selects no version by itself."""
    for _ in range(8):
        t = parse_query(text)
        if t.kind == "neutral":
            break
        for part in [_LEAD + re.escape(d.text) for d in t.dates] or [re.escape(t.trigger)]:
            text = re.sub(part, " ", text, count=1, flags=re.I)
    text = re.sub(r"\s+([,.;:?)])", r"\1", " ".join(text.split()))
    return re.sub(r",\s*,", ",", text).strip(" ,")


def compose(question: str, topic: list[str], temporal: str = "") -> str:
    """One standalone query: a context sentence (never a question sentence) with the earlier questions and the
    inherited temporal constraint, then the follow-up itself."""
    earlier = "; ".join(strip_temporal(t).rstrip(" ?.!").replace("?", ".") for t in topic)
    note = f"; version: {temporal}" if temporal else ""
    q = question.strip()
    return f"(Earlier in this conversation: {earlier}{note}). {q[:1].upper()}{q[1:]}"


def late_constraint(utterance: str) -> str | None:
    """The constraint an utterance adds to the question before it, or None: it must not be a question (no question
    mark, no interrogative opening) and must be a correction ("Actually, ...", "I meant ..."), a statement about
    what was asked ("The trip was international.", "This was for international travel."), or an instruction on
    how to read it ("Use the July 2026 version instead.", "For domestic travel only."). Returned without the words
    that only announce it ("Actually,", "I meant", "instead")."""
    text = " ".join(utterance.split())
    if "?" in text or _INTERROGATIVE.match(text):
        return None
    body, marked = text, False
    while True:  # "Sorry, actually, I meant ..."
        m = _CORRECTION.match(body)
        if not m:
            break
        body, marked = body[m.end():], True
    if not (marked or _STATEMENT.match(body) or _INSTRUCTION.match(body) or _INSTEAD.search(body)):
        return None
    body = _INSTEAD.sub("", body).strip(" .,;:!")
    return body or None


def new_words(constraint: str, earlier: list[str]) -> str:
    """The delta: the words of the constraint that the earlier questions and constraints do not already contain and
    that carry content, in the constraint's order ("the trip was international" after a question about an employee
    trip -> "international"). Empty when it adds nothing."""
    known = {w.lower() for text in earlier for w in _TOKEN.findall(text)}
    words = [w for w in _TOKEN.findall(constraint) if w.lower() not in known and w.lower() not in _FUNCTION_WORDS]
    return " ".join(words)


def topic_phrase(question: str) -> str:
    """What a question is about, for retrieval: the question without its question mark and, for "What is / are /
    about ...", without those words ("What are the travel reimbursement rules for an employee trip?" -> "travel
    reimbursement rules for an employee trip"). Any other question is its own phrase."""
    body = question.strip().rstrip("?").strip()
    m = _ASKS_FOR.match(body)
    return (body[m.end():] if m else body).strip()


def resolve(question: str, last: Turn | None, organizations: set[str], aliases: dict[str, str]) -> Resolution:
    q = " ".join(question.split())
    own = parse_query(q)
    own_temporal = own.trigger if own.kind != "neutral" else ""
    named = mentions(q, organizations, aliases)
    outside = outside_corpus(q, organizations, aliases)
    topic_text = " ".join(last.resolution.topic) if last else ""
    unknown = [] if named or outside else unknown_names(q, topic_text)
    names = list(dict.fromkeys(named + outside + unknown))
    refs = references(q)
    signals = {"references": refs, "organizations_named": named, "outside_corpus": outside, "unknown_names": unknown,
               "own_temporal": own.kind}

    # a late constraint on the question before it (not a question itself, and about the same organization, or
    # naming the first one of a topic that had none): that answer is refined (SessionController), not redone
    constraint = late_constraint(q) if last is not None and last.resolution.kind in ASKED else None
    refines = bool(constraint) and (not names or not last.resolution.organizations
                                    or set(names) <= set(last.resolution.organizations))
    # the delta: the version the constraint names, and its words that are new to the conversation
    delta = " ".join(x for x in (own_temporal, new_words(strip_temporal(constraint),
                                                         last.resolution.topic + [last.question])) if x) if refines else ""
    refines = refines and bool(delta)

    if last is None or (names and not refs and not refines):
        reason = "first turn of the session" if last is None else "names its own organization and does not refer back"
        return Resolution(SELF_CONTAINED, reason, question, [question], own_temporal, names, None, signals)

    why = (f"adds a constraint to the earlier question ({delta})" if refines
           else f"refers back ({', '.join(refs)})" if refs else "names no organization")

    def unresolved(reason: str) -> Resolution:
        return Resolution(UNRESOLVED, f"{why}, but {reason}", "", [], "", names, last.index, signals)

    if last.resolution.kind == UNRESOLVED:
        return unresolved("the previous turn could not be resolved either")
    if last.intents > 1:
        return unresolved(f"the previous turn asked {last.intents} questions, so the reference is ambiguous")
    topic_orgs = last.resolution.organizations
    if len(topic_orgs) > 1:
        return unresolved(f"the topic names several organizations ({', '.join(topic_orgs)})")
    if names and not set(names) <= set(topic_orgs) and not (refines and not topic_orgs):
        return unresolved(f"it names {', '.join(names)} while the topic is about "
                          f"{', '.join(topic_orgs) or 'no named organization'}")
    inherited = last.resolution.temporal
    if last.resolution.kind == REFINEMENT:  # its topic: the questions it refined, then the constraints so far
        asked = last.resolution.signals["questions"]
        questions, constraints = last.resolution.topic[:asked], last.resolution.topic[asked:]
        deltas = last.resolution.signals["deltas"]
    else:
        questions = ([last.resolution.topic[0]] if last.resolution.kind == FOLLOW_UP else []) + [last.question]
        constraints, deltas = [], []
    if refines:
        # the question stays the one that was asked; the constraints are its context (as earlier questions are a
        # follow-up's). What is retrieved is the delta: the new words, then the topic they apply to.
        # The version in effect is stated once, as for a follow-up: the constraint's own, else the inherited one.
        topic = questions + constraints + [constraint]
        temporal = own_temporal or inherited
        query = compose(strip_temporal(questions[-1]), questions[:-1] + constraints + [constraint], temporal)
        phrases = [topic_phrase(strip_temporal(x)) for x in questions]
        earlier_deltas = [strip_temporal(x) for x in deltas] if own_temporal else deltas
        retrieval_query = " ".join(x for x in [delta, *earlier_deltas, *phrases, "" if own_temporal else inherited] if x)
        signals |= {"constraint": constraint, "delta": delta, "deltas": [delta, *deltas], "questions": len(questions),
                    "retrieval_query": retrieval_query}
        return Resolution(REFINEMENT, why, query, topic, own_temporal or inherited,
                          list(dict.fromkeys(topic_orgs + names)), last.index, signals)
    topic = questions + constraints
    query = compose(q, topic, "" if own_temporal else inherited)
    return Resolution(FOLLOW_UP, why, query, topic, own_temporal or inherited, topic_orgs, last.index, signals)
