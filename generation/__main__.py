"""python -m generation "question" [--no-rerank] [--json]

Runs the integrated pipeline (generation.pipeline.ask: hybrid retrieval, temporal/version resolution,
optional rerank, grounded answer) with the configured provider; the integration evaluation uses the same
functions.
Requires the provider's credentials (Groq: GROQ_API_KEY) in the environment or in the project's .env file.
"""

import argparse
import json
import sys

from .env import load_env
from .pipeline import ask, load_stack
from .providers import get_provider


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question")
    parser.add_argument("--provider", default="groq")
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    load_env()  # project .env (git-ignored); an exported GROQ_API_KEY takes precedence
    answer = ask(load_stack(rerank=not args.no_rerank), get_provider(args.provider), args.question)
    print(json.dumps(answer.to_dict(), indent=1, ensure_ascii=False) if args.json else answer.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
