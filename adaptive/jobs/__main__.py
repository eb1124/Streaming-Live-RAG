"""python -m adaptive.jobs demo 'first question' 'follow-up' ... [--offline] [--session ID]
python -m adaptive.jobs worker [--url AMQP_URL]
python -m adaptive.jobs submit 'question' ... [--url AMQP_URL] [--session ID] [--timeout S]

demo    the whole flow in one process over the in-memory broker (no RabbitMQ): each question is submitted as a job,
        the worker consumes it and runs the phase 3 session controller, the client applies the job events.
        --offline answers with a stub model ("insufficient_evidence"; no network, no tokens).
worker  a worker on RabbitMQ (RABBITMQ_URL or --url; needs the "jobs" extra: pika). Runs until interrupted.
        Sessions are kept in Redis when REDIS_URL is set ("sessions" extra: redis), else in the worker's memory.
submit  a client on RabbitMQ: submits the questions in order to one session and waits for each result.
Single-quote the questions in PowerShell and bash ("$180,000" in double quotes reaches Python as ",000").
"""

import argparse
import json
import logging
import os
import sys
import uuid


class OfflineProvider:
    """Stub model for the demo: always "insufficient_evidence" (retrieval and verification still run)."""

    name, model = "offline-stub", "none"

    def complete(self, system, user, schema):
        from generation.providers import ProviderResponse

        text = json.dumps({"status": "insufficient_evidence", "claims": [], "not_in_sources": [],
                           "abstention_reason": "offline demo: no model"})
        return ProviderResponse(text=text, provider=self.name, model=self.model, finish_reason="stop")


def _controller(args):
    from adaptive.session.controller import SessionController
    from generation.env import load_env
    from generation.pipeline import load_stack
    from generation.providers import get_provider

    load_env()  # project .env (git-ignored); an exported GROQ_API_KEY takes precedence
    provider = OfflineProvider() if getattr(args, "offline", False) else get_provider(args.provider)
    return SessionController(load_stack(rerank=not args.no_rerank), provider)


def _print_job(client, job_id: str) -> None:
    from .contracts import JobStatus

    print("  states: " + " -> ".join(s.value for s in client.tracker.history[job_id]))
    status = client.status(job_id)
    if status == JobStatus.COMPLETED:
        r = client.result(job_id)
        print(f"  turn {r.turn_index + 1}: {r.resolution} ({r.resolution_reason}); answer {r.answer_status}"
              + (f" ({r.abstention_reason})" if r.abstention_reason else ""))
        print("  " + r.text.replace("\n", "\n  "))
        for c in r.citations:
            print(f"    [{c['number']}] {c['organization']} — {c['document']}, {c['pages']} · {c['chunk_id']}")
    elif status == JobStatus.FAILED:
        print(f"  error: {client.error(job_id)}")


def demo(args) -> int:
    from .broker import InMemoryBroker
    from .client import JobClient
    from .contracts import JOBS_QUEUE
    from .worker import Worker

    broker = InMemoryBroker()
    worker, client = Worker(_controller(args), broker), JobClient(broker)
    for q in args.questions:
        job_id = client.submit(q, args.session)
        print(f"\nsubmitted job {job_id} (session {args.session}): {q}\n  queued: {broker.pending(JOBS_QUEUE)} job(s)")
        worker.run(limit=1)
        client.poll()
        _print_job(client, job_id)
    return 0


def worker(args) -> int:
    from .rabbitmq import RabbitMQBroker
    from .worker import Worker

    sessions, where = None, "in memory (lost when the worker stops)"
    if os.environ.get("REDIS_URL"):  # phase 5: sessions survive worker restarts
        from adaptive.session_store.redis_store import RedisSessionStore

        sessions, where = RedisSessionStore.from_env(), "in Redis (REDIS_URL)"
    broker = RabbitMQBroker(args.url)
    w = Worker(_controller(args), broker, sessions=sessions)
    print(f"worker consuming {w.jobs_queue}; sessions {where} (Ctrl+C to stop)", flush=True)
    try:
        w.run()
    except KeyboardInterrupt:
        pass
    finally:
        broker.close()
    return 0


def submit(args) -> int:
    from .client import JobClient
    from .contracts import JobStatus
    from .rabbitmq import RabbitMQBroker

    broker = RabbitMQBroker(args.url)
    client = JobClient(broker)
    try:
        for q in args.questions:
            job_id = client.submit(q, args.session)
            print(f"\nsubmitted job {job_id} (session {args.session}): {q}", flush=True)
            if client.wait(job_id, args.timeout) not in (JobStatus.COMPLETED, JobStatus.FAILED):
                print(f"  no final event within {args.timeout} s")
                return 1
            _print_job(client, job_id)
    finally:
        broker.close()
    return 0


def main(argv=None) -> int:
    from .rabbitmq import DEFAULT_URL

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    url = os.environ.get("RABBITMQ_URL", DEFAULT_URL)
    for name in ("demo", "worker", "submit"):
        p = sub.add_parser(name)
        if name != "worker":
            p.add_argument("questions", nargs="+")
            p.add_argument("--session", default=uuid.uuid4().hex[:8])
        if name != "demo":
            p.add_argument("--url", default=url)
        if name != "submit":
            p.add_argument("--provider", default="groq")
            p.add_argument("--no-rerank", action="store_true")
        if name == "demo":
            p.add_argument("--offline", action="store_true", help="stub model: no network, no tokens")
        if name == "submit":
            p.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    return {"demo": demo, "worker": worker, "submit": submit}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
