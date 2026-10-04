"""python -m adaptive.session 'first question' 'follow-up' ... [--json]

Runs the questions in order as one conversation through the session controller (adaptive.session.controller) and
prints, per turn, how the question was resolved, the query phase 2 received, and the answer. Single-quote the
questions in PowerShell and bash: inside double quotes "$180,000" reaches Python as ",000".
Requires the provider's credentials (Groq: GROQ_API_KEY) in the environment or in the project's .env file.
"""

import argparse
import json
import sys

from generation.env import load_env
from generation.pipeline import load_stack
from generation.providers import get_provider

from .controller import SessionController
from .state import Session


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("questions", nargs="+")
    parser.add_argument("--provider", default="groq")
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    load_env()  # project .env (git-ignored); an exported GROQ_API_KEY takes precedence
    controller = SessionController(load_stack(rerank=not args.no_rerank), get_provider(args.provider))
    session = Session()
    for q in args.questions:
        turn = controller.ask(session, q)
        if args.json:
            continue
        r = turn.resolution
        print(f"\n=== Turn {turn.index + 1}: {q}\nResolution: {r.kind} ({r.reason})")
        if r.kind == "follow_up":
            print(f"Query: {r.query}")
        print(f"Temporal in effect: {r.temporal or 'none'}; organizations: {', '.join(r.organizations) or 'none'}")
        if turn.answer is not None:
            print(f"Strategy: {turn.answer.strategy}")
        print("\n" + turn.render())
        if turn.reused_citations:
            print(f"(also cited by turn {r.anchor + 1}, retrieved and verified again: {', '.join(turn.reused_citations)})")
    if args.json:
        print(json.dumps(session.to_dict(), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
