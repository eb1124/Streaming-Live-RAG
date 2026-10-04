"""Early-retrieval cases: utterances fed word by word (evaluation/early/run.py).

Eligible utterances (retrieval is required once they are complete) are not written here: they are every question of
the existing suites, unchanged: the 27 integration questions, the 16 phase 2 cases, the 2 phase 6 cases and the 8
phase 3 conversations (turn by turn, each in its own session). Whether an utterance is eligible is not declared: it is
what the controller decides on the complete utterance (Resolution.signals["retrieval_required"]).

Below are the utterances for which NO retrieval may start: written before the first run of the harness.
  after_answer   said after an answered question (CONTEXT): acknowledgements and layout requests
  unfinished     the transcript ends unfinished: the controller must wait
"""

CONTEXT = "What are Rutgers' rules for travel advances?"

ACKNOWLEDGEMENTS = [
    "Thanks, that's all.",
    "Thank you very much!",
    "Ok, got it.",
    "Great, thanks for the help.",
    "Perfect. No more questions.",
    "That's all for now, thank you.",
    "Understood, thanks.",
    "Okay, that makes sense. Thanks!",
]

LAYOUT_REQUESTS = [
    "Give me that in two bullet points.",
    "Can you put that in 3 bullets?",
    "Show me the answer as a numbered list.",
    "Rewrite it in three points.",
    "In bullet points please.",
    "Please give me that again as a numbered list.",
]

UNFINISHED = [
    "What is the...",
    "What are the",
    "How does",
    "What is the UConn...",
    "For Rutgers, what is the",
    "And what about the",
]
