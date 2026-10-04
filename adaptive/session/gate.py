"""Turn gate (deterministic, no model): is this utterance a question for the corpus at all?

  d = decide(utterance)          # Decision(kind, reason, signals)

  retrieve      a question, or anything this gate does not recognize with certainty: the existing path
                (adaptive.session.resolve, then the multi-intent controller). The default.
  wait          an utterance that is visibly unfinished: it trails off ("What is the UConn travel..."), or it has
                no closing punctuation and ends on a word, or a mark, that cannot end a request ("What are the",
                "the rules for travel and", "UConn's", "the advance,", or nothing but a question's opening words:
                "What is"). Nothing is retrieved or generated; the text is kept in the session and the next
                utterance continues it (`join`).
  suppress      an acknowledgement or a closing ("Thanks, that's all.", "Ok, got it"): every word is one of
                closing small talk, at least one marks it as such, and there is no question mark. Nothing is
                retrieved or generated and nothing is said.
  presentation  a request to lay the previous answer out differently ("Give me that in two bullet points",
                "as a numbered list please"): it names a layout this module can produce and every other word is a
                request word or a reference to the answer. Nothing is retrieved or generated: `present` re-lays the
                stored answer's verified claims, with their citation numbers, and keeps everything else of it.

Early retrieval: `predicts_retrieval(partial)` says, for a transcript that is still arriving, whether it already
carries enough content for retrieval to start before the utterance is complete. It does not replace `decide`.

The gate is conservative: a word that is not in its small vocabularies makes the utterance a question for the
existing path ("Give me the UConn advance rules in two bullet points", "Ok, what about the deadline?", "Make it
shorter", a question without a question mark that ends on a noun). Shortening, summarizing or explaining an answer
would need a model, so those requests are not presentation turns here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from adaptive.multi.controller import MultiIntentAnswer
from generation.answer import NOT_IN_SOURCES_PREFIX

from .resolve import _FUNCTION_WORDS
from .state import PRESENTATION, SUPPRESS, WAIT

RETRIEVE = "retrieve"

_TRAILS_OFF = re.compile(r"(?:\.{2,}|…)\s*$")
_CLOSED = re.compile(r"[.?!][\"'”’)\]]*\s*$")
_OPEN_MARK = re.compile(r"[,;:(/&\-–—]\s*$")
_POSSESSIVE = re.compile(r"['’]s\s*$")
# words that cannot end a request: something must follow them
_CANNOT_END = set("a an the and or but nor of between if whether my your their its our".split())
# a question's opening words: an utterance of at most MAX_OPENER_WORDS that ends on one has only begun ("What is")
_OPENER = set("what which who whom whose when where why how is are was were does do did can could should would will "
              "must".split())
MAX_OPENER_WORDS = 3

_ACK = set("thanks thank thx ty ok okay great perfect bye goodbye cheers understood noted done appreciated "
           "appreciate".split())
_ACK_PHRASE = re.compile(r"\bgot it\b|\bthat(?:'s| is| will be| would be)? all\b|\bno (?:more|further) questions?\b"
                         r"|\bnothing (?:else|more|further)\b|\ball (?:good|set|clear)\b")
_SMALL_TALK = _ACK | set(
    "you very much so a lot that that's thats is all it got for the help now i i'm im am good no more further "
    "questions question nothing else this was helpful really awesome excellent cool nice sounds fine alright yes "
    "yep yeah sure will would be do set have need needed wanted everything makes sense clear right then well "
    "just and my your answer answers information info again today".split())
MAX_ACK_WORDS = 10

_NUMBERS = {w: i for i, w in enumerate("one two three four five six seven eight nine ten".split(), 1)}
_LAYOUT = re.compile(rf"\b(?:(?P<n>\d+|{'|'.join(_NUMBERS)})\s+)?"
                     r"(?P<kind>bullet(?:ed)?(?:\s+(?:points?|list|form))?|bullets|numbered(?:\s+(?:list|points?|form))?"
                     r"|points)\b")
_REQUEST = set(
    "give show put format reformat rewrite restate present list write turn convert make break lay out down can could "
    "would will you please me us i i'd want need like it that this the answer response reply above previous last "
    "again same them those these in as into to a an of with using just now only instead form bullet bullets bulleted "
    "point points numbered number".split()) | set(_NUMBERS)
MAX_REQUEST_WORDS = 14

# early retrieval (predicts_retrieval): content words a partial transcript must carry before retrieval may start.
# Two: one content word alone is often an organization or a single ambiguous term. Chosen before any measurement.
EARLY_MIN_CONTENT_WORDS = 2
_NOT_CONTENT = _FUNCTION_WORDS | _CANNOT_END | _OPENER | _SMALL_TALK | _REQUEST


@dataclass
class Decision:
    kind: str  # retrieve | wait | suppress | presentation
    reason: str
    signals: dict = field(default_factory=dict)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


def _unfinished(text: str) -> str | None:
    """Why the utterance cannot be complete, or None."""
    if _TRAILS_OFF.search(text):
        return "it trails off"
    if _CLOSED.search(text):
        return None
    if _OPEN_MARK.search(text):
        return f"it ends on {text.rstrip()[-1]!r}"
    if _POSSESSIVE.search(text):
        return "it ends on a possessive"
    words = _words(text)
    if words and (words[-1] in _CANNOT_END or (len(words) <= MAX_OPENER_WORDS and words[-1] in _OPENER)):
        return f"it ends on {words[-1]!r}"
    return None


def _acknowledges(text: str) -> bool:
    words = _words(text)
    if "?" in text or not words or len(words) > MAX_ACK_WORDS or any(w not in _SMALL_TALK for w in words):
        return False
    return any(w in _ACK for w in words) or bool(_ACK_PHRASE.search(" ".join(words)))


def _layout(text: str) -> dict | None:
    """The layout a presentation request asks for: {"style": "bullets" | "numbered", "count": n | None}, or None."""
    words = _words(text)
    if not words or len(words) > MAX_REQUEST_WORDS or any(w not in _REQUEST and not w.isdigit() for w in words):
        return None
    m = next((m for m in _LAYOUT.finditer(" ".join(words)) if m.group("kind") != "points" or m.group("n")), None)
    if m is None:
        return None
    n = m.group("n")
    count = None if n is None else int(n) if n.isdigit() else _NUMBERS[n]
    if count is not None and count < 1:
        return None
    return {"style": "numbered" if m.group("kind").startswith("numbered") else "bullets", "count": count}


def decide(utterance: str) -> Decision:
    text = " ".join(utterance.split())
    if _acknowledges(text):
        return Decision(SUPPRESS, "an acknowledgement or a closing: nothing is asked", {"retrieval_required": False})
    layout = _layout(text)
    if layout:
        return Decision(PRESENTATION, "asks for the previous answer in another layout: nothing new is asked",
                        {"retrieval_required": False, "layout": layout})
    why = _unfinished(text)
    if why:
        return Decision(WAIT, f"the utterance is not finished: {why}", {"retrieval_required": False})
    return Decision(RETRIEVE, "a question for the corpus", {"retrieval_required": True})


def content_words(text: str) -> list[str]:
    """The words of `text` that say what it is about: not function words, question openers, request words or
    closing small talk (the vocabularies of this module and of adaptive.session.resolve)."""
    return [w for w in _words(text) if w not in _NOT_CONTENT and not w.isdigit()]


def predicts_retrieval(partial: str) -> bool:
    """Retrieval-intent prediction on a transcript that is still arriving (no model): True once what has been said
    so far already carries EARLY_MIN_CONTENT_WORDS content words, i.e. it is about something the corpus must be
    asked. An acknowledgement ("Thanks, that's"), a layout request ("Give me that in two") or a question's opening
    words ("What are the") have none, however long they get, so they never trigger; a question does as soon as its
    subject starts to appear ("What are the UConn travel"). This predicts only that retrieval will be needed: the
    decision on the finished utterance is `decide`'s, unchanged (an utterance that trails off still waits)."""
    return len(content_words(partial)) >= EARLY_MIN_CONTENT_WORDS


def join(pending: str, fragment: str) -> str:
    """The utterance so far: what was waiting, continued by the new fragment. A fragment that repeats what was
    waiting and goes on (the whole utterance sent again, longer) replaces it."""
    head = _TRAILS_OFF.sub("", " ".join(pending.split())).rstrip()
    tail = " ".join(fragment.split())
    if not head or tail.lower().startswith(head.lower()):
        return tail
    return f"{head} {tail}"


def _sentence(claim: dict) -> str:
    """A claim as the answer text writes it (generation.answer, adaptive.multi.controller): its text, closed, with
    its citation numbers; after its version label when the answer was split by version."""
    text = claim["text"] if claim["text"].endswith((".", "?", "!")) else claim["text"] + "."
    prefix = f"{claim['version_label']}: " if claim.get("version_label") else ""
    return prefix + text + "".join(f"[{n}]" for n in claim["citations"])


def present(answer: MultiIntentAnswer, layout: dict) -> MultiIntentAnswer:
    """The answer with its text laid out as asked, from its verified claims: one item per claim, or, when fewer items
    are asked for than there are claims, the claims in order in that many items. No claim is dropped, reworded or
    added, so an answer with fewer claims than asked for gives fewer items. Citations, claims, evidence and every
    other field are the stored answer's. An answer without claims (an abstention) is returned as it is."""
    sentences = [_sentence(cl) for cl in answer.claims]
    if not sentences:
        return answer
    n = min(layout.get("count") or len(sentences), len(sentences))
    size, extra = divmod(len(sentences), n)
    items, at = [], 0
    for k in range(n):
        step = size + (1 if k < extra else 0)
        items.append(" ".join(sentences[at:at + step]))
        at += step
    numbered = layout.get("style") == "numbered"
    lines = [f"{k}. {item}" if numbered else f"- {item}" for k, item in enumerate(items, 1)]
    if answer.not_in_sources:  # the stored gaps, stated once after the items, as the answer text states them
        lines.append(NOT_IN_SOURCES_PREFIX + "; ".join(g.rstrip(".") for g in answer.not_in_sources) + ".")
    return replace(answer, text="\n".join(lines))
