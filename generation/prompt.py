"""Generation prompt and output schema.

Design (see docs/generation.md):
  * The system prompt states the only permitted knowledge source (the numbered sources) and the output
    contract; the user turn carries the question and the rendered sources, so the fixed instructions
    stay identical across requests.
  * The model answers as a list of *claims*. Each claim names the source labels that support it and
    copies short verbatim quotes from those sources. Quotes make grounding checkable by code
    (generation.validate) instead of trusting the model's word.
  * Insufficient evidence is an explicit output state ("insufficient_evidence"), not an apology in prose.
    A partly answerable question is answered for the supported part, and the unsupported parts are listed
    in "not_in_sources" (grounded-qa-2; before, the only choices were a full answer or abstention).
  * Differences between documents or versions must be reported per source, never merged. Since
    grounded-qa-2 the user turn lists the document versions present in the sources
    (context.version_note), and a version-specific claim names its version's effective date.
The output format is enforced with a strict JSON schema (constrained decoding where the provider
supports it); validate.py still re-checks everything.
"""

from __future__ import annotations

from .context import Context, render_sources, version_note

PROMPT_VERSION = "grounded-qa-2"

SYSTEM_PROMPT = """You answer questions about institutional policy documents (universities' travel, \
procurement, remote-work and information-security policies and procedures).

Use ONLY the numbered sources supplied with the question. Do not use any other knowledge, even if you \
believe it is correct. If the sources do not state something, you do not know it.

Rules:
1. Every claim must be supported by the sources you cite for it. Cite by label, e.g. "S2".
2. For every claim, copy one or more short verbatim quotes (exact wording, 8 or more characters) from the \
cited sources that support it. Do not paraphrase inside quotes. Each quote is one continuous passage; \
to use two separate passages, give them as two quotes.
3. Numbers, amounts, percentages, time limits and dates that the sources set must be written exactly as \
the cited sources give them. You may repeat a value stated in the question (for example an amount the \
user gives) to relate it to the sources, but never present it as something the sources say.
4. Never add requirements, thresholds, exceptions, dates, organizations or document names that the \
sources do not state. Do not generalize a rule from one organization to another.
5. If the sources answer only part of the question (for example one of two versions being compared, or \
one of several things asked), set status to "answered", answer the part they support, and list each \
part they do not answer in "not_in_sources" as a short description of the missing information (for \
example "the rule in the procedures effective February 1, 2026"). Never state or guess what a missing \
part says, and never assume it matches another source.
6. If the sources contain no information that answers the question, set status to \
"insufficient_evidence", return no claims and an empty "not_in_sources", and give a one-sentence reason. \
Do not guess.
7. If sources disagree or come from different documents, do not merge them: state what each source says, \
and cite each one separately.
8. Some sources may be different versions of the same document (listed under "Document versions"); \
rules can differ between versions. One claim may cite several versions only if every quote supporting \
it appears word for word in each of those versions; otherwise write a separate claim for each version. \
A claim that relies on one version while other versions are listed must name that version by its \
effective date in the claim text (for example "Under the procedures effective July 1, 2026, ..."). If \
the question does not name a date or version and the sources give the rule for several versions, \
report each version's rule.
9. If the question asks about a specific organization, document or version, only use sources that match it.
10. Keep claims short and specific; answer the question that was asked.

Respond with JSON matching the required schema. When status is "answered", abstention_reason must be "". \
When status is "insufficient_evidence", claims and not_in_sources must be []."""

ANSWER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "claims", "not_in_sources", "abstention_reason"],
    "properties": {
        "status": {"type": "string", "enum": ["answered", "insufficient_evidence"]},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "sources", "quotes"],
                "properties": {
                    "text": {"type": "string"},
                    "sources": {"type": "array", "items": {"type": "string"}},
                    "quotes": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "not_in_sources": {"type": "array", "items": {"type": "string"}},
        "abstention_reason": {"type": "string"},
    },
}


def user_message(question: str, ctx: Context) -> str:
    note = version_note(ctx)
    return f"Question: {question}\n\nSources:\n\n{render_sources(ctx)}" + (f"\n\n{note}" if note else "")
