"""Phase 5 offline evaluation: the phase 3 conversations through the phase 4 job workflow on a session store, compared
with the committed phase 3 evaluation and with the phase 3 controller called directly. Real retrieval stack, in-memory
broker, NO language model.

  python -m evaluation.session_store.run            # writes results/offline.json and results/offline.md

For each conversation (evaluation/session/cases.py), three runs, each with its own recording stub model:
  direct     SessionController.ask on one in-memory Session (phase 3 as is)
  memory     jobs -> Worker on an InMemorySessionStore (phase 4's default)
  restarted  jobs -> a NEW Worker and a NEW RedisSessionStore for every turn, over one in-process stand-in for the
             Redis client: each turn starts from the encoded session only (codec round trip + worker restart)
Checked per turn, for memory and restarted: the turn equals the committed phase 3 evaluation
(evaluation/session/results/offline.json: resolution kind, reason, rewritten query, temporal constraint in effect,
organizations, phase 2 strategy, selected versions, the chunks shown to the model) and the direct run (answer
status, text, citations); the job completed. Per conversation: identical model input in all three runs, and the
stored session equals the direct one field for field.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
PHASE3 = HERE.parent / "session" / "results" / "offline.json"
COMPARED = ("kind", "reason", "query", "temporal_in_effect", "organizations", "strategy", "selected_versions", "context")


class LocalRedis:
    """In-process stand-in for the Redis client: get/set/delete over bytes (what RedisSessionStore uses)."""

    def __init__(self):
        self.data: dict[str, bytes] = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = bytes(value)

    def delete(self, key):
        self.data.pop(key, None)


def facts(turn) -> dict:
    """The fields the phase 3 evaluation recorded for a turn, from a Turn."""
    r, a = turn.resolution, turn.answer
    selected = {}
    for i in a.intents if a else []:
        for series, docs in i["selected_versions"].items():
            selected.setdefault(series, sorted(set(selected.get(series, [])) | set(docs)))
    return {"kind": r.kind, "reason": r.reason, "query": r.query, "temporal_in_effect": r.temporal,
            "organizations": r.organizations, "strategy": a.strategy if a else None, "selected_versions": selected,
            "context": list(dict.fromkeys(s["chunk_id"] for p in (a.phase1 if a else []) for s in p.sources_considered))}


def run_conversation(conv: dict, reference: dict, stack) -> dict:
    from adaptive.jobs.broker import InMemoryBroker
    from adaptive.jobs.client import JobClient
    from adaptive.jobs.contracts import JobStatus
    from adaptive.jobs.worker import Worker
    from adaptive.session.controller import SessionController
    from adaptive.session.state import Session
    from adaptive.session_store.redis_store import RedisSessionStore
    from adaptive.session_store.store import InMemorySessionStore
    from evaluation.multi_intent.run import RecordingStub

    sid = conv["id"]
    stubs = {"direct": RecordingStub(), "memory": RecordingStub(), "restarted": RecordingStub()}
    direct, direct_session = SessionController(stack, stubs["direct"]), Session()
    broker, backend = InMemoryBroker(), LocalRedis()
    client = JobClient(broker)
    memory = Worker(SessionController(stack, stubs["memory"]), broker, sessions=InMemorySessionStore())
    restarted_ctl = SessionController(stack, stubs["restarted"])
    turns = []
    for k, spec in enumerate(conv["turns"]):
        d = direct.ask(direct_session, spec["question"])
        expected = {f: reference["turns"][k][f] for f in COMPARED}
        rec = {"question": spec["question"], "kind": d.resolution.kind, "checks": []}
        if facts(d) != expected:
            rec["checks"].append("direct run differs from the committed phase 3 evaluation")
        runs = {"memory": memory,
                "restarted": Worker(restarted_ctl, broker, sessions=RedisSessionStore(client=backend))}
        for name, worker in runs.items():
            job_id = client.submit(spec["question"], sid)
            worker.run()
            client.poll()
            r = client.result(job_id)
            stored = worker.sessions.get(sid)
            t = stored.turns[-1] if stored and stored.turns else None
            ok = {"completed": client.status(job_id) == JobStatus.COMPLETED,
                  "turn_index": bool(r) and r.turn_index == k,
                  "phase3": t is not None and facts(t) == expected,
                  "answer": bool(r) and (r.answer_status, r.text, [c["chunk_id"] for c in r.citations]) == (
                      d.status, d.text, d.cited_chunks)}
            rec[name] = ok
            rec["checks"] += [f"{name}: {what}" for what, good in ok.items() if not good]
        turns.append(rec)
    same_input = stubs["direct"].calls == stubs["memory"].calls == stubs["restarted"].calls
    stored_ok = {name: asdict(store.get(sid)) == asdict(direct_session)
                 for name, store in (("memory", memory.sessions), ("restarted", RedisSessionStore(client=backend)))}
    return {"id": sid, "tags": conv["tags"], "turns": turns, "model_calls": len(stubs["direct"].calls),
            "identical_model_input": same_input, "stored_session_equals_direct": stored_ok,
            "encoded_bytes": len(backend.data.get(f"adaptiverag:session:{sid}", b""))}


def write_md(convs: list[dict]) -> str:
    turns = [t for c in convs for t in c["turns"]]
    ok = sum(not t["checks"] for t in turns)
    same = sum(c["identical_model_input"] for c in convs)
    stored = sum(all(c["stored_session_equals_direct"].values()) for c in convs)
    mark = lambda d: "✓" if all(d.values()) else "✗ " + ", ".join(k for k, v in d.items() if not v)  # noqa: E731
    out = ["# Phase 5 offline evaluation (no language model)", "",
           "The phase 3 conversations through the phase 4 job workflow on a session store, compared with the committed "
           "phase 3 evaluation (evaluation/session/results/offline.json) and the phase 3 controller called directly. "
           "memory = one worker on an InMemorySessionStore; restarted = a new worker and a new RedisSessionStore for "
           "every turn over an in-process stand-in for the Redis client (each turn starts from the encoded session). "
           "Real retrieval stack, stub model; see evaluation/session_store/run.py.", "",
           f"## {ok}/{len(turns)} turns pass; {same}/{len(convs)} conversations with identical model input; "
           f"{stored}/{len(convs)} stored sessions equal to the direct one", "",
           "| conversation | turn | question | resolution | memory | restarted | checks |", "|---|---|---|---|---|---|---|"]
    for c in convs:
        for k, t in enumerate(c["turns"], 1):
            out.append(f"| {c['id']} | {k} | {t['question']} | {t['kind']} | {mark(t['memory'])} | "
                       f"{mark(t['restarted'])} | {'; '.join(t['checks']) or 'ok'} |")
    out += ["", "✓ = completed job, turn index, resolution/query/strategy/versions/context equal to phase 3, answer "
                "and citations equal to the direct run.", "",
            "| conversation | model calls | identical model input | stored = direct (memory / restarted) | encoded session |",
            "|---|---|---|---|---|"]
    out += [f"| {c['id']} | {c['model_calls']} | {c['identical_model_input']} | "
            f"{c['stored_session_equals_direct']['memory']} / {c['stored_session_equals_direct']['restarted']} | "
            f"{c['encoded_bytes']:,} bytes |" for c in convs]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    import os

    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from evaluation.session.cases import CONVERSATIONS
    from generation.pipeline import load_stack

    reference = {c["id"]: c for c in json.loads(PHASE3.read_text(encoding="utf-8"))["conversations"]}
    stack = load_stack(rerank=True)
    convs = []
    for conv in CONVERSATIONS:
        rec = run_conversation(conv, reference[conv["id"]], stack)
        convs.append(rec)
        for k, t in enumerate(rec["turns"], 1):
            print(f"{conv['id']:14} {k} {t['kind']:14} {'; '.join(t['checks']) or 'ok'}", flush=True)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "offline.json").write_text(json.dumps({"conversations": convs}, indent=1, ensure_ascii=False) + "\n",
                                          encoding="utf-8")
    md = write_md(convs)
    (RESULTS / "offline.md").write_text(md, encoding="utf-8")
    print(md)
    failed = (any(t["checks"] for c in convs for t in c["turns"]) or not all(c["identical_model_input"] for c in convs)
              or not all(all(c["stored_session_equals_direct"].values()) for c in convs))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
