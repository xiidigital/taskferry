"""Portable, JSON-only task message format (ADR-0008).

A taskport backend serializes an enqueue into this envelope, ships it to the
provider, and the consumer side rebuilds and runs the task. Only JSON-safe data
crosses the wire — pass identifiers, not objects (section 23).
"""

from __future__ import annotations

import importlib
from typing import Any, TypedDict

from django.tasks.base import Task
from taskport.core import (
    Correlation,
    ensure_json_serializable,
)

MESSAGE_VERSION = "1"


class TaskMessage(TypedDict, total=False):
    """The on-the-wire shape of an enqueued task."""

    taskport: str
    module_path: str
    name: str
    args: list[Any]
    kwargs: dict[str, Any]
    priority: int
    queue_name: str
    run_after: str | None
    correlation: dict[str, str] | None


def build_message(
    task: Task,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    correlation: Correlation | None = None,
) -> TaskMessage:
    """Build a JSON-serializable message from a Django Task and its arguments."""
    ensure_json_serializable(list(args))
    ensure_json_serializable(dict(kwargs))
    run_after = task.run_after.isoformat() if task.run_after is not None else None
    message: TaskMessage = {
        "taskport": MESSAGE_VERSION,
        "module_path": task.module_path,
        "name": task.name,
        "args": list(args),
        "kwargs": dict(kwargs),
        "priority": task.priority,
        "queue_name": task.queue_name,
        "run_after": run_after,
        "correlation": correlation.to_headers() if correlation else None,
    }
    return message


def resolve_task(message: TaskMessage) -> Task:
    """Resolve the Task object referenced by a message.

    Raises ``LookupError`` if the task cannot be found — the consumer decides how
    to react (dead-letter, log, etc.).
    """
    module_path = message.get("module_path", "")
    name = message.get("name", "")
    module_name = module_path.rsplit(".", 1)[0] if "." in module_path else module_path
    try:
        module = importlib.import_module(module_name)
        candidate = getattr(module, name)
    except (ImportError, AttributeError) as exc:
        raise LookupError(f"cannot resolve task {module_path!r}: {exc}") from exc
    if not isinstance(candidate, Task):
        raise LookupError(f"{module_path!r} did not resolve to a Task")
    return candidate


__all__ = ["MESSAGE_VERSION", "TaskMessage", "build_message", "resolve_task"]
