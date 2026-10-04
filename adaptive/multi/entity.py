"""Entity alignment of retrieved evidence (deterministic, no model): the named thing a question asks about.

  retrieve = AlignedRetriever(generation.pipeline.retrieve, organizations, aliases)     # retrieve()'s signature
  r = retrieve(stack, "What is the Rutgers University Travel Card suspension rule?")

A question names an entity when it writes a term as a name: a capitalized phrase ("Travel Card", "Purchasing Card")
or a cased compound ("PCard", "T-Card"). Organization names, their short names and institution-style names are not
entities (adaptive.multi.decompose knows them), and a question written without capitals names none. The entity is the
last two words of the phrase: a modifier and a head ("travel" + "card").

A chunk
  names the entity        when its text has the modifier and the head together, in any case ("employee travel card",
                          "Travel Cards"), or the cased compound of the modifier's initial and the head ("TCard").
                          For a compound entity ("PCard"): that compound, or a phrase whose modifier starts with its
                          letter ("purchasing card").
  names another one       when its text has, written as a name, the head with a different modifier: a capitalized
                          phrase ("Purchasing Card") or a cased compound ("PCard"). Lower-case wording ("a credit
                          card", "the card") is not a name and never counts.

Nothing happens unless the retrieved evidence is CONTESTED: some chunk names another entity of the head and not the
one asked about. Then, and only then:

  * chunks that name only another entity are removed: evidence about a different named thing cannot be shown to the
    model, quoted or cited for this question (a PCard rule cannot ground a Travel Card answer; for a PCard question
    the same chunk names the entity and stays). The other chunks keep their order, ranks and scores.
  * if what would then be shown (the first MAX_SOURCES chunks) names the asked entity in fewer than MIN_NAMING
    chunks (of the question's organizations, when it names any), the entity is SCARCE in it: the retrieval is taken
    again from the whole reranked pool (WIDE_K candidates instead of the top RETRIEVE_K), the same removal is
    applied, and the pool's first MIN_NAMING chunks that name the entity are placed first; every other chunk follows
    in the retrieval's own order. Ranks are renumbered in that order and the top k are returned. A chunk that names
    the asked thing under an abbreviation ("TCard") is found this way although the cross-encoder ranked it below
    the top k; at most MIN_NAMING - 1 of the chunks that would have been shown give way to it.

A chunk's text here is what the index holds for it (Chunk.retrieval_text: document title, section path, text), so a
chunk under the heading "University Travel Card Penalties" names the Travel Card even where its text says "the card".

The retrieval, the temporal resolution and the cross-encoder scores are the retriever's, unchanged; only which of its
candidates are kept, and in which order, changes. An uncontested retrieval is returned as it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from generation import config as C
from generation.context import Evidence
from generation.pipeline import RETRIEVE_K, Retrieval

from .decompose import _INSTITUTION, _NOT_A_NAME, mentions

WIDE_K = 20  # the reranked pool (retrieval.rerank.CANDIDATE_POOL): every candidate the cross-encoder scored
MIN_NAMING = 2  # shown chunks that must name the asked entity, or it is scarce: one alone is often only its definition

_WORD = r"[A-Z][a-z]+(?:-[A-Za-z]+)*"
_PHRASE = re.compile(rf"\b{_WORD}(?: {_WORD})+\b")  # "Travel Card", "University Travel Card"
_COMPOUND = re.compile(r"\b([A-Z])-?([A-Z][a-z]+)\b")  # "PCard", "P-Card", "TCard"
# capitalized words before a head that are not a modifier naming a kind ("Each Card", "The Card")
_NOT_A_MODIFIER = _NOT_A_NAME | {"Each", "Every", "Any", "All", "No", "This", "That", "These", "Those", "Such", "Your",
                                 "Their", "Its", "His", "Her", "Our", "One", "Per", "New", "Other", "Another", "Same"}


@dataclass(frozen=True)
class Entity:
    text: str  # as the question wrote it: "Travel Card", "PCard"
    modifier: str  # lower case: "travel"; for a compound, its letter: "p"
    head: str  # lower case, singular: "card"
    compound: bool = False


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def entities(query: str, organizations: set[str], aliases: dict[str, str]) -> list[Entity]:
    """The entities the query names, in order, each once. Organizations and institutions are removed first."""
    text = " ".join(query.split())
    for name in sorted(organizations, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", " . ", text, flags=re.I)
    for alias in sorted(aliases, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(alias)}(?!\w)", " . ", text)
    text = _INSTITUTION.sub(" . ", text)
    found: list[tuple[int, Entity]] = []
    for m in _PHRASE.finditer(text):
        words = m.group(0).split()
        while words and words[0] in _NOT_A_NAME:  # "What Travel Card ...": the first word starts the sentence
            words.pop(0)
        if len(words) >= 2:
            found.append((m.start(), Entity(" ".join(words[-2:]), words[-2].lower(), _singular(words[-1].lower()))))
    for m in _COMPOUND.finditer(text):
        found.append((m.start(), Entity(m.group(0), m.group(1).lower(), _singular(m.group(2).lower()), True)))
    out: dict[tuple[str, str, bool], Entity] = {}
    for _, e in sorted(found, key=lambda x: x[0]):
        out.setdefault((e.modifier, e.head, e.compound), e)
    return list(out.values())


def names(entity: Entity, text: str) -> bool:
    """The text names this entity (see the module docstring)."""
    head, initial = re.escape(entity.head), re.escape(entity.modifier[0].upper())
    if re.search(rf"\b{initial}-?{head.capitalize()}s?\b", text):  # the cased compound: "TCard", "P-Card"
        return True
    modifier = rf"{re.escape(entity.modifier)}[a-z]+" if entity.compound else re.escape(entity.modifier)
    return bool(re.search(rf"(?<![A-Za-z]){modifier}\s+{head}s?(?![A-Za-z])", text, re.I))


def names_other(entity: Entity, text: str) -> list[str]:
    """Names in the text of another thing of the entity's head: "PCard", "Purchasing Card" for "Travel Card"."""
    head = re.escape(entity.head.capitalize())
    out = [m.group(0) for m in re.finditer(rf"\b([A-Z])-?{head}s?\b", text) if m.group(1).lower() != entity.modifier[0]]
    for m in re.finditer(rf"\b([A-Z][a-z]+)\s+{head}s?\b", text):
        word = m.group(1)
        same = word[0].lower() == entity.modifier if entity.compound else word.lower() == entity.modifier
        if not same and word not in _NOT_A_MODIFIER:
            out.append(m.group(0))
    return list(dict.fromkeys(out))


def _named(entities_: list[Entity], text: str) -> bool:
    return any(names(e, text) for e in entities_)


def other_only(entities_: list[Entity], text: str) -> bool:
    """The text names another thing of some entity's head, and none of the question's entities of that head."""
    return any(names_other(e, text) and not _named([x for x in entities_ if x.head == e.head], text) for e in entities_)


def contested(evidence: list[Evidence], entities_: list[Entity]) -> bool:
    return any(other_only(entities_, e.chunk.retrieval_text) for e in evidence)


def keep(evidence: list[Evidence], entities_: list[Entity]) -> list[Evidence]:
    """Evidence without the chunks that name only another entity; order, ranks and scores unchanged."""
    return [e for e in evidence if not other_only(entities_, e.chunk.retrieval_text)]


def _leads(e: Evidence, entities_: list[Entity], own: set[str]) -> bool:
    """The chunk names an asked entity, and is of the organizations the question names (when it names any)."""
    return _named(entities_, e.chunk.retrieval_text) and (not own or e.chunk.organization is None or e.chunk.organization in own)


def scarce(evidence: list[Evidence], entities_: list[Entity], organizations: list[str] | None = None) -> bool:
    """Fewer than MIN_NAMING of the chunks that would be shown (the first MAX_SOURCES) name an asked entity."""
    own = set(organizations or [])
    return sum(_leads(e, entities_, own) for e in evidence[:C.MAX_SOURCES]) < MIN_NAMING


def align(evidence: list[Evidence], entities_: list[Entity], organizations: list[str] | None = None) -> list[Evidence]:
    """`keep`, then the first MIN_NAMING chunks that name an asked entity (of `organizations`, the ones the question
    names, when there are any) placed first, every other chunk after them in its own order; ranks renumbered."""
    kept, own = keep(evidence, entities_), set(organizations or [])
    lead = [e for e in kept if _leads(e, entities_, own)][:MIN_NAMING]
    first = {id(e) for e in lead}
    ordered = lead + [e for e in kept if id(e) not in first]
    return [replace(e, rank=rank) for rank, e in enumerate(ordered, 1)]


class AlignedRetriever:
    """A retriever (generation.pipeline.retrieve or a drop-in) whose contested retrievals are entity-aligned."""

    def __init__(self, retriever, organizations: set[str], aliases: dict[str, str]):
        self.retriever, self.organizations, self.aliases = retriever, set(organizations), aliases

    def __call__(self, stack, query: str, k: int = RETRIEVE_K) -> Retrieval:
        r = self.retriever(stack, query, k)
        named = entities(query, self.organizations, self.aliases)
        if not named or not contested(r.evidence, named):
            return r
        organizations = mentions(query, self.organizations, self.aliases)
        kept = keep(r.evidence, named)
        if not scarce(kept, named, organizations):
            return replace(r, evidence=kept)
        wide = self.retriever(stack, query, max(k, WIDE_K))
        return replace(wide, evidence=align(wide.evidence, named, organizations)[:k])
