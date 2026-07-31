"""The consuming side — three shapes, because SQS is deployed three ways.

```mermaid
flowchart LR
    Q["SQS queue"]

    Q --> L["AWS Lambda<br/>process_event(event)"]
    Q --> C["ECS / Fargate<br/>poll_forever(...)"]
    Q --> O["your own loop<br/>process_message(body)"]
```

Taskport supplies **no worker daemon**. What it supplies is the function that
turns a message body into a function call; how that function gets invoked is your
deployment's business, and SQS already solves the hard parts — visibility
timeouts, redrive to a dead-letter queue, at-least-once delivery.

:func:`poll_forever` is the one concession, for an ECS or Fargate container that
just needs a loop. It is forty lines, uses long polling, deletes on success and
lets failures fall back to SQS's own redrive policy. It is deliberately not a
worker framework: no concurrency model, no prefetch, no heartbeats. If you need
those, you need a worker, and SQS consumers are a well-trodden path.

Idempotency
-----------

SQS delivery is at-least-once and Taskport does not change that. A handler that
runs twice must be safe to run twice.
"""

from __future__ import annotations

import contextlib
import json
import logging
import signal
from collections.abc import Callable
from types import FrameType
from typing import Any

from taskport.envelope import execute_envelope
from taskport.errors import SerializationError
from taskport.functions import FunctionRegistry

logger = logging.getLogger("taskport.sqs")

DEFAULT_WAIT_SECONDS = 20
"""Long-polling wait. 20s is SQS's maximum and the cheapest way to poll."""


def process_message(
    body: str | bytes | dict[str, Any], *, registry: FunctionRegistry | None = None
) -> Any:
    """Run the task described by one SQS message body.

    Args:
        body: The raw ``Body`` — a JSON string, bytes, or an already-decoded dict.
        registry: Where task names resolve. **Give it an allowlist**: the name
            arrives off a queue, and resolving it means importing a module.
    """
    if isinstance(body, str | bytes):
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise SerializationError(f"SQS message body is not valid JSON: {exc}") from exc
    else:
        payload = body
    return execute_envelope(payload, registry=registry)


def process_event(
    event: dict[str, Any], *, registry: FunctionRegistry | None = None
) -> dict[str, list[dict[str, str]]]:
    """Run every task in a Lambda SQS event, and report partial failures.

    Returns the ``batchItemFailures`` structure Lambda expects when the event
    source mapping has **partial batch responses** enabled — so one poisonous
    message does not force the whole batch to be redelivered:

        def handler(event, context):
            return process_event(event, registry=REGISTRY)

    Without partial batch responses configured, an exception is the way to make
    Lambda retry, so re-raise instead::

        result = process_event(event, registry=REGISTRY)
        if result["batchItemFailures"]:
            raise RuntimeError(result)
    """
    failures: list[dict[str, str]] = []
    for record in event.get("Records", []):
        message_id = str(record.get("messageId", ""))
        try:
            process_message(record.get("body", ""), registry=registry)
        except Exception:
            logger.exception("taskport: SQS message %s failed", message_id)
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}


def poll_forever(
    client: Any,
    queue_url: str,
    *,
    registry: FunctionRegistry | None = None,
    wait_seconds: int = DEFAULT_WAIT_SECONDS,
    max_messages: int = 10,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    """Long-poll a queue and run what arrives, until told to stop.

    For an ECS/Fargate container or a plain process. Deletes a message only after
    its task succeeds; a failure is left for SQS to redeliver and eventually
    redrive to your dead-letter queue, which is the behaviour its retry
    configuration already describes.

    Args:
        client: A ``boto3.client("sqs")``.
        queue_url: The queue to drain.
        registry: Task-name resolver. Give it an allowlist.
        wait_seconds: Long-poll duration, up to SQS's maximum of 20.
        max_messages: Batch size, up to SQS's maximum of 10.
        should_stop: Called between batches; return ``True`` to exit. Defaults to
            a SIGTERM/SIGINT handler, so a container stops cleanly at the end of
            a batch rather than mid-task.

    Returns:
        The number of messages successfully processed.
    """
    stop = should_stop if should_stop is not None else _sigterm_stopper()
    processed = 0

    while not stop():
        response = client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_seconds,
        )
        for message in response.get("Messages", []) or []:
            try:
                process_message(message.get("Body", ""), registry=registry)
            except Exception:
                # Leave it on the queue. SQS's visibility timeout and redrive
                # policy decide what happens next — that is what they are for.
                logger.exception(
                    "taskport: SQS message %s failed; leaving it for redelivery",
                    message.get("MessageId"),
                )
                continue
            client.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
            processed += 1

    logger.info("taskport: SQS consumer stopping after %d message(s)", processed)
    return processed


def _sigterm_stopper() -> Callable[[], bool]:
    """A ``should_stop`` that flips on SIGTERM/SIGINT, for graceful shutdown."""
    stopped = False

    def _handle(signum: int, frame: FrameType | None) -> None:
        nonlocal stopped
        stopped = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        # Installing a handler is only possible on the main thread; a consumer
        # started from a worker thread simply does not get graceful shutdown.
        with contextlib.suppress(ValueError):
            signal.signal(sig, _handle)

    return lambda: stopped


__all__ = [
    "DEFAULT_WAIT_SECONDS",
    "poll_forever",
    "process_event",
    "process_message",
]
