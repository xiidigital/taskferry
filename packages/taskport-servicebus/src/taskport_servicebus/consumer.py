"""The consuming side for Service Bus.

```mermaid
flowchart LR
    Q["Service Bus queue"]

    Q --> F["Azure Function<br/>process_message(msg.get_body())"]
    Q --> C["Container App<br/>receive_forever(...)"]
```

As everywhere else, Taskport supplies no worker. Service Bus already handles lock
renewal, delivery counts and dead-lettering; this module turns a message body
into a function call and nothing more.

Delivery is at-least-once. Handlers must be idempotent.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from taskport.envelope import execute_envelope
from taskport.errors import SerializationError
from taskport.functions import FunctionRegistry

logger = logging.getLogger("taskport.servicebus")


def process_message(
    body: str | bytes | dict[str, Any], *, registry: FunctionRegistry | None = None
) -> Any:
    """Run the task described by one Service Bus message body.

    Accepts what the various SDK shapes hand you: a ``str``, ``bytes``, an
    already-decoded dict, or the generator ``ServiceBusReceivedMessage.body``
    returns.

    Args:
        body: The message body.
        registry: Where task names resolve. **Give it an allowlist** — the name
            arrives off a queue.
    """
    payload = _decode(body)
    return execute_envelope(payload, registry=registry)


def _decode(body: Any) -> Any:
    """Normalise the several shapes an Azure message body arrives in."""
    if isinstance(body, dict):
        return body
    if not isinstance(body, str | bytes | bytearray):
        # ServiceBusReceivedMessage.body is a generator of byte chunks.
        try:
            body = b"".join(bytes(chunk) for chunk in body)
        except TypeError as exc:  # pragma: no cover - hostile input
            raise SerializationError(
                f"cannot read a Service Bus message body of type {type(body).__name__}"
            ) from exc
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SerializationError(f"Service Bus message body is not valid JSON: {exc}") from exc


def receive_forever(
    client: Any,
    queue_name: str,
    *,
    registry: FunctionRegistry | None = None,
    max_wait_time: int = 30,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    """Receive and run tasks until told to stop. For a Container App or a process.

    Completes a message only after its task succeeds; a failure abandons it, so
    Service Bus redelivers and — once ``MaxDeliveryCount`` is reached — moves it
    to the dead-letter queue. That policy lives on the queue, which is where it
    belongs.

    Args:
        client: A ``ServiceBusClient``.
        queue_name: The queue to drain.
        registry: Task-name resolver. Give it an allowlist.
        max_wait_time: Seconds to wait for a batch before looping.
        should_stop: Called between batches; return ``True`` to exit.

    Returns:
        The number of messages successfully processed.
    """
    stop = should_stop if should_stop is not None else (lambda: False)
    processed = 0

    with client.get_queue_receiver(queue_name=queue_name) as receiver:
        while not stop():
            for message in receiver.receive_messages(max_wait_time=max_wait_time):
                try:
                    process_message(message.body, registry=registry)
                except Exception:
                    logger.exception(
                        "taskport: Service Bus message %s failed; abandoning for redelivery",
                        getattr(message, "message_id", "?"),
                    )
                    receiver.abandon_message(message)
                    continue
                receiver.complete_message(message)
                processed += 1

    logger.info("taskport: Service Bus consumer stopping after %d message(s)", processed)
    return processed


__all__ = ["process_message", "receive_forever"]
