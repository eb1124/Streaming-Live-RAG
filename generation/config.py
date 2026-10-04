"""Fixed generation-layer defaults. Chosen before any generation run; not tuned on test results."""

# Context assembly
MAX_SOURCES = 6  # retrieved chunks passed to the LLM (retrieval R@5 >= 0.97 on the retrieval benchmark)
MAX_CONTEXT_TOKENS = 2400  # estimated tokens of source text (chunking.tokens.count_tokens)

# Evidence-sufficiency gate (before any LLM call)
MIN_SOURCES = 1
# When cross-encoder scores are present: at least one candidate must score >= this logit.
# 0.0 is the cross-encoder's own decision boundary (sigmoid 0.5 = "relevant"), not a tuned value.
MIN_RERANK_LOGIT = 0.0

# Claim verification
MIN_QUOTE_CHARS = 8  # a supporting quote shorter than this proves nothing

# Provider defaults (evaluation run)
DEFAULT_PROVIDER = "groq"
GROQ_MODEL = "openai/gpt-oss-20b"
TEMPERATURE = 0.0
SEED = 1234
REASONING_EFFORT = "medium"  # gpt-oss: low | medium | high
MAX_COMPLETION_TOKENS = 4096
# gpt-oss spends completion tokens on reasoning before it writes the answer, so a long answer can exhaust 4096 and
# leave the structured output unfinished (Groq: HTTP 400 json_validate_failed, or finish_reason "length"). That one
# case is asked again, once, with this budget (the model's own limit on Groq is 65536). Nothing else is retried.
RETRY_MAX_COMPLETION_TOKENS = 16384

ABSTENTION_TEXT = "The retrieved documents do not contain enough evidence to answer this question."
