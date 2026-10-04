"""The message-broker boundary (AdaptiveRAG phase 4): bytes in, bytes out, nothing about RAG.

  broker.publish(queue, body)
  broker.consume(queue, handler, limit=None, timeout=None) -> number of messages handled

Delivery semantics shared by every broker: a message is acknowledged when the handler returns. If the handler raises,
the exception is logged and the message is rejected WITHOUT requeue (dropped): there is no automatic retry and no
dead-letter queue in this phase. The worker's handler turns pipeline exceptions into FAILED job events itself, so
only messages it cannot attribute to a job reach this path.

InMemoryBroker: FIFO queues in one process; consume() delivers what is waiting, synchronously, then returns. Used by
the tests, the offline evaluation and the demo. RabbitMQBroker (adaptive/jobs/rabbitmq.py) is the same interface
over a real server; nothing else imports pika.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Callable, Protocol

log = logging.getLogger(__name__)

Handler = Callable[[bytes], None]


class Broker(Protocol):
    def publish(self, queue: str, body: bytes) -> None: ...

    def consume(self, queue: str, handler: Handler, limit: int | None = None, timeout: float | None = None) -> int:
        """Deliver messages to `handler` until `limit` were handled or the queue stays idle for `timeout` seconds
        (None: RabbitMQ waits forever; the in-memory broker returns when the queue is empty)."""
        ...


class InMemoryBroker:
    def __init__(self):
        self.queues: dict[str, deque[bytes]] = defaultdict(deque)
        self.published: list[tuple[str, bytes]] = []  # everything ever published, for inspection
        self.rejected: list[tuple[str, bytes, str]] = []  # (queue, body, error) of messages whose handler raised

    def publish(self, queue: str, body: bytes) -> None:
        if not isinstance(body, bytes):
            raise TypeError("message bodies are bytes")
        self.queues[queue].append(body)
        self.published.append((queue, body))

    def consume(self, queue: str, handler: Handler, limit: int | None = None, timeout: float | None = None) -> int:
        n = 0
        q = self.queues[queue]
        while q and (limit is None or n < limit):
            body = q.popleft()
            try:
                handler(body)
            except Exception as e:  # rejected without requeue, as RabbitMQBroker does
                log.exception("handler failed on a message from %s; message rejected (not requeued)", queue)
                self.rejected.append((queue, body, f"{type(e).__name__}: {e}"))
            n += 1
        return n

    def pending(self, queue: str) -> int:
        return len(self.queues[queue])
