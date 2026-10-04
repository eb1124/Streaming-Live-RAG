"""Phase 4 offline evaluation: the phase 3 conversations through the asynchronous job workflow, compared turn by turn
with calling the phase 3 session controller directly. Real retrieval stack, in-memory broker, NO language model.

  python -m evaluation.jobs.run            # writes results/offline.json and results/offline.md

For each conversation (evaluation/session/cases.py):
  direct  SessionController.ask(session, question) for each turn, with its own recording stub model
  jobs    JobClient.submit -> InMemoryBroker -> Worker (SessionController.ask) -> job events -> JobClient
Checked per turn: the job went queued -> processing -> completed; the resolution (kind and the query phase 2
receives) equals the direct one and the case's expected kind; the job result (answer status, text, citations) equals
the direct turn. Checked per conversation: the model input of every call is byte-identical on both paths.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def run_conversation(conv: dict, stack) -> dict:
    from adaptive.jobs.broker import InMemoryBroker
    from adaptive.jobs.client import JobClient
    from adaptive.jobs.contracts import JobStatus
    from adaptive.jobs.worker import Worker
    from adaptive.session.controller import SessionController
    from adaptive.session.state import Session
    from evaluation.multi_intent.run import RecordingStub

    s_direct, s_jobs = RecordingStub(), RecordingStub()
    direct, session = SessionController(stack, s_direct), Session()
    broker = InMemoryBroker()
    worker, client = Worker(SessionController(stack, s_jobs), broker), JobClient(broker)
    turns = []
    for spec in conv["turns"]:
        d = direct.ask(session, spec["question"])
        job_id = client.submit(spec["question"], conv["id"])
        worker.run()
        client.poll()
        states = [s.value for s in client.tracker.history[job_id]]
        r = client.result(job_id)
        w = worker.sessions[conv["id"]].turns[-1] if r else None
        rec = {"question": spec["question"], "job_id": job_id, "states": states,
               "resolution": r.resolution if r else None, "expected_resolution": spec["kind"],
               "same_resolution": bool(w) and (w.resolution.kind, w.resolution.query) == (d.resolution.kind,
                                                                                          d.resolution.query),
               "same_answer": bool(r) and (r.answer_status, r.text, [c["chunk_id"] for c in r.citations]) == (
                   d.status, d.text, d.cited_chunks),
               "answer_status": r.answer_status if r else None, "error": client.error(job_id), "checks": []}
        if states != [JobStatus.QUEUED.value, JobStatus.PROCESSING.value, JobStatus.COMPLETED.value]:
            rec["checks"].append(f"states {states}")
        if rec["resolution"] != spec["kind"]:
            rec["checks"].append(f"resolution {rec['resolution']}, expected {spec['kind']}")
        if not rec["same_resolution"]:
            rec["checks"].append("resolution differs from the direct controller")
        if not rec["same_answer"]:
            rec["checks"].append("answer differs from the direct controller")
        turns.append(rec)
    return {"id": conv["id"], "tags": conv["tags"], "turns": turns, "model_calls": len(s_jobs.calls),
            "identical_model_input": s_direct.calls == s_jobs.calls, "broker_rejected": len(broker.rejected)}


def write_md(convs: list[dict]) -> str:
    turns = [t for c in convs for t in c["turns"]]
    ok = sum(not t["checks"] for t in turns)
    same = sum(c["identical_model_input"] for c in convs)
    out = ["# Phase 4 offline evaluation (no language model)", "",
           "The phase 3 conversations (evaluation/session/cases.py) submitted as jobs through the in-memory broker "
           "to a worker running the phase 3 session controller, compared with calling that controller directly. Real "
           "retrieval stack, stub model; see evaluation/jobs/run.py.", "",
           f"## {ok}/{len(turns)} turns pass; {same}/{len(convs)} conversations with byte-identical model input", "",
           "| conversation | turn | question | job states | resolution | = direct resolution | = direct answer | checks |",
           "|---|---|---|---|---|---|---|---|"]
    for c in convs:
        for k, t in enumerate(c["turns"], 1):
            out.append(f"| {c['id']} | {k} | {t['question']} | {' → '.join(t['states'])} | {t['resolution']} | "
                       f"{t['same_resolution']} | {t['same_answer']} | {'; '.join(t['checks']) or 'ok'} |")
    out += ["", "| conversation | model calls | identical model input | broker rejections |", "|---|---|---|---|"]
    out += [f"| {c['id']} | {c['model_calls']} | {c['identical_model_input']} | {c['broker_rejected']} |" for c in convs]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    import os

    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from evaluation.session.cases import CONVERSATIONS
    from generation.pipeline import load_stack

    stack = load_stack(rerank=True)
    convs = []
    for conv in CONVERSATIONS:
        rec = run_conversation(conv, stack)
        convs.append(rec)
        for k, t in enumerate(rec["turns"], 1):
            print(f"{conv['id']:14} {k} {' -> '.join(t['states']):35} {t['resolution']:14} "
                  f"{'; '.join(t['checks']) or 'ok'}", flush=True)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "offline.json").write_text(json.dumps({"conversations": convs}, indent=1, ensure_ascii=False) + "\n",
                                          encoding="utf-8")
    md = write_md(convs)
    (RESULTS / "offline.md").write_text(md, encoding="utf-8")
    print(md)
    failed = any(t["checks"] for c in convs for t in c["turns"]) or not all(c["identical_model_input"] for c in convs)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
