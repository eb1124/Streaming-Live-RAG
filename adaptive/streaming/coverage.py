"""Coverage check (AdaptiveRAG phase 6): does the evidence the model would be shown cover the request? Rules only.

  coverage = assess(question, evidence, relevance, resolution, organizations, aliases)

This is a heuristic over existing signals, not a measure of semantic sufficiency or a learned confidence. It looks
only at the context the answerer would assemble from the evidence (generation.context.assemble: at most MAX_SOURCES
sources), and at three things about it:

  relevance     the cross-encoder score of a source for the question itself (the existing reranker; a source is
                relevant at MIN_RERANK_LOGIT = 0.0 or above, the answerer's own low-relevance threshold). A score
                for a refined query never counts: refinements only widen the candidates.
  organizations the corpus organizations the question names (adaptive.multi.decompose.mentions: full names and the
                aliases in config/organization_aliases.toml), and institutions it names that the corpus does not
                hold (decompose.outside_corpus).
  versions      the temporal resolution's flags (requested_version_unavailable).

Decision, in this order:
  structurally_unresolved  the question names only institutions outside the corpus; or nothing in the context is
                           relevant and the version the question asks for does not exist. No retrieval can change it.
  sufficient (unmeasured)  the evidence has no cross-encoder scores (a stack without a reranker): coverage cannot be
                           measured, and the frozen rule applies (the answerer proceeds when anything was retrieved).
  insufficient             nothing was retrieved; or no source in the context reaches the relevance threshold; or a
                           corpus organization the question names has no relevant source in the context.
  sufficient               otherwise: something relevant, and something relevant for every organization named.
"""

from __future__ import annotations

from adaptive.multi.decompose import mentions, outside_corpus
from generation import config as C
from generation.context import Evidence, assemble

from .state import INSUFFICIENT, STRUCTURAL, SUFFICIENT, Coverage


def assess(question: str, evidence: list[Evidence], relevance: dict[str, float | None], resolution,
           organizations: set[str], aliases: dict[str, str]) -> Coverage:
    named = mentions(question, organizations, aliases)
    outside = outside_corpus(question, organizations, aliases)
    ctx = assemble(evidence)
    scores = {s.chunk.chunk_id: relevance.get(s.chunk.chunk_id) for s in ctx.sources}
    by_org: dict[str, float] = {}
    for s in ctx.sources:
        score = scores[s.chunk.chunk_id]
        if score is not None and s.chunk.organization:
            by_org[s.chunk.organization] = max(by_org.get(s.chunk.organization, score), score)
    unavailable = [f for f in resolution.flags if f.startswith("requested_version_unavailable")]
    measured = [x for x in scores.values() if x is not None]
    best = max(measured, default=None)
    gaps = [o for o in named if by_org.get(o, float("-inf")) < C.MIN_RERANK_LOGIT]
    signals = {"named_organizations": named, "outside_corpus": outside, "context_size": len(ctx.sources),
               "best_relevance": best, "relevance_by_organization": by_org, "gaps": gaps,
               "unavailable_versions": unavailable, "threshold": C.MIN_RERANK_LOGIT}

    if outside and not named:
        return Coverage(STRUCTURAL, f"names only institutions outside the corpus ({', '.join(outside)})", signals)
    if not ctx.sources:
        return Coverage(INSUFFICIENT, "nothing was retrieved", signals)
    if best is None:
        return Coverage(SUFFICIENT, "no cross-encoder scores: coverage cannot be measured (the frozen rule applies)",
                        signals)
    if best < C.MIN_RERANK_LOGIT:
        if unavailable:
            return Coverage(STRUCTURAL, "the requested version does not exist and nothing relevant was found", signals)
        return Coverage(INSUFFICIENT, f"no source in the context reaches the relevance threshold (best {best:.2f})",
                        signals)
    if gaps:
        return Coverage(INSUFFICIENT, f"no relevant source for {', '.join(gaps)}", signals)
    return Coverage(SUFFICIENT, "relevant sources" + (f" for {', '.join(named)}" if named else ""), signals)
