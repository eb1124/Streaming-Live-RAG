"""Phase 7 offline evaluation: queries through the service architecture (api -> phase 4 job -> orchestrator ->
retrieval / generation services) compared with the direct pipeline (SessionController), turn by turn. Real retrieval
stack, NO language model.

  python -m evaluation.services.run            # writes results/offline.json and results/offline.md

The services run in one process (services.inprocess: FastAPI TestClient, in-memory broker); the direct pipeline and
the generation service each get their own recording stub model ("insufficient_evidence"; no network, no tokens).
Suites:
  phase2   the 16 phase 2 cases, one session each                     (frozen retrieval)
  phase3   the 8 phase 3 conversations                                 (frozen retrieval)
  phase5   the phase 3 conversations with a new api, orchestrator and RedisSessionStore for every turn, over one
           in-process stand-in for the Redis client: each turn starts from the stored session only
  phase6   the phase 3 conversations and the 2 phase 6 cases           (the phase 6 loop in the retrieval service)
Checked per turn: HTTP 200 and a completed job; the QueryResponse (resolution, rewritten query, temporal constraint,
organizations, strategy, answer strategies, evidence shown to the model, answer, citations) equals what the direct
turn gives. Per conversation: identical model input, and the stored session equals the direct session.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
IDS = ("request_id", "job_id", "session_id")


class LocalRedis:
    def __init__(self):
        self.data: dict[str, bytes] = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = bytes(value)

    def delete(self, key):
        self.data.pop(key, None)


def conversations():
    from evaluation.multi_intent.cases import CASES as P2
    from evaluation.session.cases import CONVERSATIONS
    from evaluation.streaming.cases import CASES as P6

    p3 = [(c["id"], [t["question"] for t in c["turns"]]) for c in CONVERSATIONS]
    return {"phase2": ([(c["id"], [c["question"]]) for c in P2], "single", False),
            "phase3": (p3, "single", False),
            "phase5": (p3, "single", True),
            "phase6": (p3 + [(c["id"], [c["question"]]) for c in P6], "iterative", False)}


def run_conversation(stack, sid: str, questions: list[str], mode: str, restart: bool) -> dict:
    from adaptive.session.controller import SessionController
    from adaptive.session.state import Session
    from adaptive.session_store.redis_store import RedisSessionStore
    from adaptive.streaming.controller import IterativeRetriever
    from evaluation.multi_intent.run import RecordingStub
    from services.contracts import QueryResponse
    from services.inprocess import InProcess

    direct_stub, service_stub = RecordingStub(), RecordingStub()
    direct = SessionController(stack, direct_stub, retriever=IterativeRetriever() if mode == "iterative" else None)
    session = Session()
    backend = LocalRedis()

    def deployment():
        store = RedisSessionStore(client=backend) if restart else None
        return InProcess(stack, service_stub, mode, sessions=store)

    local = deployment()
    turns = []
    for k, q in enumerate(questions):
        if restart and k:
            local = deployment()  # new api, orchestrator, store instance: only the stored bytes carry the session
        turn = direct.ask(session, q)
        expected = {f: v for f, v in QueryResponse.of_turn("r", "j", "s", turn).model_dump().items() if f not in IDS}
        r = local.api.post("/query", json={"session_id": sid, "question": q})
        rec = {"question": q, "http": r.status_code, "kind": turn.resolution.kind, "checks": []}
        if r.status_code != 200:
            rec["checks"].append(f"HTTP {r.status_code}: {r.text[:200]}")
        else:
            got = QueryResponse.model_validate(r.json())
            rec |= {"job_id": got.job_id, "status": got.status, "strategy": got.strategy,
                    "evidence": len(got.evidence), "citations": len(got.citations)}
            diff = sorted(f for f, v in got.model_dump().items() if f not in IDS and expected[f] != v)
            if got.session_id != sid:
                rec["checks"].append("session_id changed")
            if diff:
                rec["checks"].append(f"differs from the direct pipeline in {diff}")
        turns.append(rec)
    stored = local.sessions.get(sid)
    return {"id": sid, "mode": mode, "restart": restart, "turns": turns, "model_calls": len(direct_stub.calls),
            "identical_model_input": direct_stub.calls == service_stub.calls,
            "stored_session_equals_direct": stored is not None and asdict(stored) == asdict(session)}


def write_md(suites: dict) -> str:
    out = ["# Phase 7 offline evaluation (no language model)", "",
           "Queries through the services (api → phase 4 job → orchestrator → retrieval / generation services, all "
           "in one process: services.inprocess) compared with the direct pipeline (SessionController). Real retrieval "
           "stack, stub model; see evaluation/services/run.py.", ""]
    total = [t for convs in suites.values() for c in convs for t in c["turns"]]
    ok = sum(not t["checks"] for t in total)
    convs_all = [c for convs in suites.values() for c in convs]
    out += [f"## {ok}/{len(total)} turns equal to the direct pipeline; "
            f"{sum(c['identical_model_input'] for c in convs_all)}/{len(convs_all)} conversations with identical model "
            f"input; {sum(c['stored_session_equals_direct'] for c in convs_all)}/{len(convs_all)} stored sessions equal "
            "to the direct session", ""]
    for name, convs in suites.items():
        turns = [t for c in convs for t in c["turns"]]
        out += [f"### {name} ({convs[0]['mode']} retrieval{', new deployment every turn' if convs[0]['restart'] else ''}): "
                f"{sum(not t['checks'] for t in turns)}/{len(turns)} turns", "",
                "| conversation | turn | question | resolution | strategy | evidence | = input | = session | checks |",
                "|---|---|---|---|---|---|---|---|---|"]
        for c in convs:
            for k, t in enumerate(c["turns"], 1):
                out.append(f"| {c['id']} | {k} | {t['question']} | {t['kind']} | {t.get('strategy') or '–'} | "
                           f"{t.get('evidence', '–')} | {c['identical_model_input']} | "
                           f"{c['stored_session_equals_direct']} | {'; '.join(t['checks']) or 'ok'} |")
        out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    import logging
    import os

    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    logging.disable(logging.INFO)
    from generation.pipeline import load_stack

    stack = load_stack(rerank=True)
    suites = {}
    for name, (convs, mode, restart) in conversations().items():
        suites[name] = [run_conversation(stack, sid, qs, mode, restart) for sid, qs in convs]
        print(f"{name} done", flush=True)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "offline.json").write_text(json.dumps(suites, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    md = write_md(suites)
    (RESULTS / "offline.md").write_text(md, encoding="utf-8")
    print(md)
    failed = any(t["checks"] for convs in suites.values() for c in convs for t in c["turns"]) or not all(
        c["identical_model_input"] and c["stored_session_equals_direct"] for convs in suites.values() for c in convs)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
