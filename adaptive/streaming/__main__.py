"""python -m adaptive.streaming 'question' [--max-rounds N] [--offline] [--json]

Runs the bounded iterative retrieval controller (adaptive.streaming.controller) and prints the answer, then the
retrieval: each round's query, strategy, evidence counts and coverage decision, and why the loop stopped.
--offline answers with a stub model ("insufficient_evidence"; no network, no tokens); retrieval still runs.
Requires the provider's credentials (Groq: GROQ_API_KEY) in the environment or the project's .env file otherwise.
Single-quote the question in PowerShell and bash ("$180,000" in double quotes reaches Python as ",000").
"""

import argparse
import json
import sys

from .controller import DEFAULT_MAX_ROUNDS, StreamingController


class OfflineProvider:
    name, model = "offline-stub", "none"

    def complete(self, system, user, schema):
        from generation.providers import ProviderResponse

        text = json.dumps({"status": "insufficient_evidence", "claims": [], "not_in_sources": [],
                           "abstention_reason": "offline: no model"})
        return ProviderResponse(text=text, provider=self.name, model=self.model, finish_reason="stop")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question")
    parser.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS)
    parser.add_argument("--provider", default="groq")
    parser.add_argument("--offline", action="store_true", help="stub model: no network, no tokens")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    from generation.env import load_env
    from generation.pipeline import load_stack
    from generation.providers import get_provider

    load_env()  # project .env (git-ignored); an exported GROQ_API_KEY takes precedence
    provider = OfflineProvider() if args.offline else get_provider(args.provider)
    result = StreamingController(load_stack(rerank=True), provider, args.max_rounds).run(args.question)
    if args.json:
        print(json.dumps(result.to_dict(), indent=1, ensure_ascii=False, default=str))
        return 0
    t = result.trace
    print(result.answer.render())
    print(f"\nRetrieval: {'iterative' if t.iterations > 1 else 'single round'}, {t.iterations} of at most "
          f"{t.max_rounds} rounds; stopped: {t.stop_reason}")
    for r in t.rounds:
        print(f"  round {r.iteration} [{r.strategy}{': ' + r.target if r.target else ''}] {r.query}")
        print(f"    retrieved {len(r.retrieved)}, new {len(r.new)}, excluded versions {len(r.excluded_versions)}, "
              f"retained {len(r.retained)}, context {len(r.context)}"
              + (f", promoted {', '.join(r.promoted)}" if r.promoted else ""))
        print(f"    coverage: {r.coverage.decision} ({r.coverage.reason}) -> {r.decision}")
    for s in t.skipped:
        print(f"  not issued [{s['strategy']}] {s['query']}: {s['why']}")
    print(f"Answer strategy: {result.answer.decision.strategy}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
