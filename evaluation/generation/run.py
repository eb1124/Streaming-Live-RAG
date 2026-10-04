"""Run the generation/evidence suite against the configured live provider.

  python -m evaluation.generation.run                 one pass (Groq, openai/gpt-oss-20b)
  python -m evaluation.generation.run --runs 2        two passes + run-to-run agreement

Writes evaluation/generation/results/run-<n>.json and results/report.md. Exit code 2 if the provider's
credentials are missing (nothing is sent), 1 if any case check fails.
This is a small behavioral suite (13 cases), not an answer-quality benchmark.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .cases import CASES, resolve

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def check(case: dict, answer, ctx_ids: list[str], chunks_by_doc: dict) -> list[str]:
    fails = []
    got = "answered" if answer.status == "answered" else "abstained"
    if got != case["status"]:
        fails.append(f"status {got} (expected {case['status']}; reason {answer.abstention_reason}: {answer.abstention_detail[:160]})")
    cited = [c.chunk_id for c in answer.citations]
    if any(cid not in ctx_ids for cid in cited):
        fails.append("citation to a chunk that was not in the context")
    if answer.status == "answered":
        for r in case.get("must_cite", []):
            if resolve(r, chunks_by_doc) not in cited:
                fails.append(f"missing citation: {r['doc']} {r['quote'][:50]!r}")
        for r in case.get("must_not_cite", []):
            if resolve(r, chunks_by_doc) in cited:
                fails.append(f"forbidden citation: {r['doc']} {r['quote'][:50]!r}")
        for s in case.get("must_contain", []):
            if s.casefold() not in answer.text.casefold():
                fails.append(f"answer lacks {s!r}")
    return fails


def run_once(provider, stack, chunks_by_doc: dict, by_id: dict) -> list[dict]:
    from generation.answer import GroundedAnswerer
    from generation.context import Evidence
    from generation.pipeline import retrieve_evidence

    answerer, out = GroundedAnswerer(provider), []
    for case in CASES:
        if case["context"] == "retrieve":
            evidence = retrieve_evidence(stack, case["question"])
        else:
            evidence = [Evidence(by_id[resolve(r, chunks_by_doc)], i, None, "supplied")
                        for i, r in enumerate(case["context"], 1)]
        t = time.perf_counter()
        answer = answerer.answer(case["question"], evidence)
        elapsed = time.perf_counter() - t
        ctx_ids = [s["chunk_id"] for s in answer.sources_considered]
        fails = check(case, answer, ctx_ids, chunks_by_doc)
        out.append({"id": case["id"], "category": case["category"], "passed": not fails, "failures": fails,
                    "seconds": round(elapsed, 2), "answer": answer.to_dict(), "rendered": answer.render()})
        print(f"{case['id']:20} {'PASS' if not fails else 'FAIL'}  {answer.status:9} {'; '.join(fails)[:140]}")
    return out


def agreement(runs: list[list[dict]]) -> list[dict]:
    rows = []
    for i, case in enumerate(CASES):
        answers = [r[i]["answer"] for r in runs]
        rows.append({"id": case["id"],
                     "same_status": len({a["status"] for a in answers}) == 1,
                     "same_citations": len({tuple(c["chunk_id"] for c in a["citations"]) for a in answers}) == 1,
                     "same_text": len({a["text"] for a in answers}) == 1})
    return rows


def write_report(runs: list[list[dict]], meta: dict) -> str:
    out = ["# Generation / evidence suite", "",
           f"Provider {meta['provider']}, model {meta['model']}, prompt {meta['prompt_version']}, settings {meta['settings']}. "
           f"{len(CASES)} cases, {len(runs)} run(s). This is a behavioral grounding/citation/abstention check, "
           "not an answer-quality benchmark.", ""]
    for n, run in enumerate(runs, 1):
        out += [f"## Run {n}: {sum(r['passed'] for r in run)}/{len(run)} passed", "",
                "| case | category | result | status | citations | failures |", "|---|---|---|---|---|---|"]
        for r in run:
            a = r["answer"]
            cites = ", ".join(c["chunk_id"].split("::")[0][:18] + "::" + c["chunk_id"].split("::")[1] for c in a["citations"]) or "—"
            out.append(f"| {r['id']} | {r['category']} | {'PASS' if r['passed'] else 'FAIL'} | {a['status']}"
                       f"{' (' + a['abstention_reason'] + ')' if a['abstention_reason'] else ''} | {cites} | {'; '.join(r['failures']) or ''} |")
        out.append("")
    if len(runs) > 1:
        ag = agreement(runs)
        out += ["## Run-to-run agreement", "",
                f"* same outcome: {sum(r['same_status'] for r in ag)}/{len(ag)}; same citations: {sum(r['same_citations'] for r in ag)}/{len(ag)}; "
                f"identical text: {sum(r['same_text'] for r in ag)}/{len(ag)}", ""]
    out += ["## Answers (run 1)", ""]
    for r in runs[0]:
        out += [f"### {r['id']}", "", "```", r["rendered"], "```", ""]
    report = "\n".join(out) + "\n"
    (RESULTS / "report.md").write_text(report, encoding="utf-8")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    from generation.env import load_env

    load_env()  # project .env (git-ignored); an exported GROQ_API_KEY takes precedence
    if not os.environ.get("GROQ_API_KEY"):
        print("BLOCKED: GROQ_API_KEY is not set (environment or .env); no request was sent. Set it and re-run.")
        return 2
    from chunking.pipeline import load_chunks
    from generation import config as C
    from generation.pipeline import load_stack
    from generation.prompt import PROMPT_VERSION
    from generation.providers import get_provider

    chunks_by_doc = load_chunks()
    by_id = {c.chunk_id: c for cs in chunks_by_doc.values() for c in cs}
    stack = load_stack(rerank=True)
    provider = get_provider("groq")
    runs = [run_once(provider, stack, chunks_by_doc, by_id) for _ in range(args.runs)]
    RESULTS.mkdir(exist_ok=True)
    for n, run in enumerate(runs, 1):
        (RESULTS / f"run-{n}.json").write_text(json.dumps(run, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    meta = {"provider": provider.name, "model": C.GROQ_MODEL, "prompt_version": PROMPT_VERSION, "settings": provider.settings}
    print(write_report(runs, meta))
    return 0 if all(r["passed"] for run in runs for r in run) else 1


if __name__ == "__main__":
    raise SystemExit(main())
