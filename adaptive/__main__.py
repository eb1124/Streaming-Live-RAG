"""python -m adaptive "question" [--no-rerank] [--json]

Runs the adaptive retrieval controller (adaptive.controller) over the frozen integrated pipeline and prints the
answer, the strategy (single | split_by_version) and the signals behind the decision. The frozen path without
the controller is `python -m generation`.
Requires the provider's credentials (Groq: GROQ_API_KEY) in the environment or in the project's .env file.
"""

import argparse
import json
import sys

from generation.env import load_env
from generation.pipeline import load_stack
from generation.providers import get_provider

from .controller import AdaptiveController


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question")
    parser.add_argument("--provider", default="groq")
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    load_env()  # project .env (git-ignored); an exported GROQ_API_KEY takes precedence
    controller = AdaptiveController(load_stack(rerank=not args.no_rerank), get_provider(args.provider))
    answer = controller.run(args.question)
    if args.json:
        print(json.dumps(answer.to_dict(), indent=1, ensure_ascii=False))
        return 0
    d = answer.decision
    print(answer.render())
    print(f"\nStrategy: {d.strategy}\nReason: {d.reason}\nSignals: {json.dumps(d.signals, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
