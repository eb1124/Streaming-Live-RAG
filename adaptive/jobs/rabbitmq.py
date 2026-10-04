"""RabbitMQ implementation of the Broker interface (AdaptiveRAG phase 4). The only module that imports pika
(optional dependency: pip install -e ".[jobs]").

  broker = RabbitMQBroker("amqp://guest:guest@localhost:5672/%2F")    # or RABBITMQ_URL

Durable queues on the default exchange, persistent JSON messages, prefetch 1 (one unacknowledged message per
consumer: a single worker handles one job at a time, in queue order). A message is acknowledged after its handler
returns; if the handler raises, it is logged and rejected without requeue (no retry loop, no dead-letter queue).
"""

from __future__ import annotations

import logging

from .broker import Handler

log = logging.getLogger(__name__)
DEFAULT_URL = "amqp://guest:guest@localhost:5672/%2F"


class RabbitMQBroker:
    def __init__(self, url: str = DEFAULT_URL):
        import pika

        self._pika = pika
        self.connection = pika.BlockingConnection(pika.URLParameters(url))
        self.channel = self.connection.channel()
        self.channel.basic_qos(prefetch_count=1)
        self._declared: set[str] = set()

    def _declare(self, queue: str) -> None:
        if queue not in self._declared:
            self.channel.queue_declare(queue=queue, durable=True)
            self._declared.add(queue)

    def publish(self, queue: str, body: bytes) -> None:
        self._declare(queue)
        props = self._pika.BasicProperties(delivery_mode=2, content_type="application/json")  # persistent
        self.channel.basic_publish(exchange="", routing_key=queue, body=body, properties=props)

    def consume(self, queue: str, handler: Handler, limit: int | None = None, timeout: float | None = None) -> int:
        self._declare(queue)
        n = 0
        try:
            for method, _props, body in self.channel.consume(queue, inactivity_timeout=timeout):
                if method is None:  # idle for `timeout` seconds
                    break
                try:
                    handler(body)
                except Exception:
                    log.exception("handler failed on a message from %s; message rejected (not requeued)", queue)
                    self.channel.basic_nack(method.delivery_tag, requeue=False)
                else:
                    self.channel.basic_ack(method.delivery_tag)
                n += 1
                if limit is not None and n >= limit:
                    break
        finally:
            self.channel.cancel()  # unacknowledged prefetched messages go back to the queue
        return n

    def purge(self, queue: str) -> None:
        self._declare(queue)
        self.channel.queue_purge(queue)

    def close(self) -> None:
        if self.connection.is_open:
            self.connection.close()
