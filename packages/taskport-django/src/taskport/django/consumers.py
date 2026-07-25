"""Consumer helpers for pull backends (SQS on Lambda / ECS / Fargate).

Example AWS Lambda handler::

    from taskport.django.consumers import process_sqs_event

    def handler(event, context):
        process_sqs_event(event)

Handlers must be idempotent — SQS delivery is at-least-once (ADR-0008).
"""

from __future__ import annotations

import json
from typing import Any

from .execution import execute_task_message
from .message import TaskMessage


def process_sqs_message(body: str | dict[str, Any]) -> Any:
    """Execute a single task from an SQS message body (JSON string or dict)."""
    message: TaskMessage = json.loads(body) if isinstance(body, str) else body  # type: ignore[assignment]
    return execute_task_message(message)


def process_sqs_event(event: dict[str, Any]) -> list[Any]:
    """Execute every task in a Lambda SQS event (``event["Records"]``)."""
    return [process_sqs_message(record["body"]) for record in event.get("Records", [])]


__all__ = ["process_sqs_event", "process_sqs_message"]
