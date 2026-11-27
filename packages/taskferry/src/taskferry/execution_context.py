"""Ambient metadata for the currently executing Taskferry task.

Adapters set only facts they can observe. Consumers may use this context to
bridge execution metadata into their own task framework without coupling the
Taskferry core to that framework.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime

from .execution import ExecutionId


@dataclass(frozen=True, slots=True)
class TaskExecutionContext:
    """Provider-observed metadata for one delivery attempt."""

    execution_id: ExecutionId | None = None
    attempt: int | None = None
    worker_id: str | None = None
    started_at: datetime | None = None


_CURRENT: ContextVar[TaskExecutionContext | None] = ContextVar(
    "taskferry_task_execution_context", default=None
)


def current_task_execution() -> TaskExecutionContext | None:
    """Return the execution metadata set by the active backend, if any."""
    return _CURRENT.get()


@contextmanager
def use_task_execution(context: TaskExecutionContext) -> Iterator[None]:
    """Set execution metadata for the duration of a worker call."""
    token: Token[TaskExecutionContext | None] = _CURRENT.set(context)
    try:
        yield
    finally:
        _CURRENT.reset(token)


__all__ = ["TaskExecutionContext", "current_task_execution", "use_task_execution"]
