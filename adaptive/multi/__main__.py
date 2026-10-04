"""python -m adaptive.multi "question" [--no-rerank] [--json]

Runs the multi-intent controller (adaptive.multi.controller) over the phase 1 controller and the frozen integrated
pipeline, and prints the answer, the strategy (delegate | fused | per_intent), the reason, and each intent with its
sub-query and coverage. Phase 1 alone is `python -m adaptive`; the frozen path is `python -m generation`.
Requires the provider's credentials (Groq: GROQ_API_KEY) in the environment or in the project's .env file.
"""

import argparse
import json
import sys

from generation.env import load_env
from generation.pipeline import load_stack
from generation.providers import get_provider

from .controller import MultiIntentController


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question")
    parser.add_argument("--provider", default="groq")
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    load_env()  # project .env (git-ignored); an exported GROQ_API_KEY takes precedence
    controller = MultiIntentController(load_stack(rerank=not args.no_rerank), get_provider(args.provider))
    answer = controller.run(args.question)
    if args.json:
        print(json.dumps(answer.to_dict(), indent=1, ensure_ascii=False))
        return 0
    print(answer.render())
    print(f"\nStrategy: {answer.strategy}\nReason: {answer.reason}")
    for i in answer.intents:
        print(f"  intent {i['index'] + 1}: {i['sub_query']!r} admissible={i['admissible']} "
              f"covered={i['covered_by_question_context']} status={i['status']} citations={i['citations']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
