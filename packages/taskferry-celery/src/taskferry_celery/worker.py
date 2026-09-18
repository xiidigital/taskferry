"""The worker side — one Celery task that runs every Taskferry message.

```mermaid
sequenceDiagram
    participant B as broker
    participant W as celery worker
    participant D as taskferry:dispatch
    participant REG as FunctionRegistry
    participant FN as your function

    B->>W: deliver task
    W->>D: dispatch(message)
    D->>REG: resolve "package.module:function"
    REG-->>D: callable
    D->>FN: call(*args, **kwargs)
    FN-->>D: value
    D-->>W: done
```

Taskferry supplies **no worker**. You run ``celery -A myapp worker``; this module
only registers the function that worker calls. Reservation, concurrency, prefetch,
acks and shutdown are Celery's.

Security
--------

The dispatcher resolves a task *name* that arrived from the broker. If untrusted
input can reach your broker, that resolution is an arbitrary-import primitive, so
pass a :class:`~taskferry.functions.FunctionRegistry` with an allowlist::

    registry = FunctionRegistry(allowed_modules=["myapp.tasks"])
    register_dispatcher(app, registry=registry)

Return values
-------------

The dispatcher returns whatever the task returned. Celery stores it only if a
result backend is configured, and this adapter does not advertise ``RESULT``.
Write results where they belong instead of relying on the queue to keep them.
"""

from __future__ import annotations

import logging
from typing import Any

from taskferry.envelope import execute_envelope
from taskferry.functions import FunctionRegistry

from .message import TaskferryMessage, build_message, check_version, decode_retry

logger = logging.getLogger("taskferry.celery")

DEFAULT_DISPATCH_TASK = "taskferry:dispatch"


def execute_message(
    message: TaskferryMessage,
    *,
    registry: FunctionRegistry | None = None,
) -> Any:
    """Resolve and run one Taskferry message. Pure of Celery, so unit-testable."""
    check_version(message)
    return execute_envelope(message, registry=registry)


def register_dispatcher(
    app: Any,
    *,
    name: str = DEFAULT_DISPATCH_TASK,
    registry: FunctionRegistry | None = None,
    **task_options: Any,
) -> Any:
    """Register the Taskferry dispatcher on a Celery ``app``.

    Call once in the module your worker loads::

        from celery import Celery
        from taskferry_celery import register_dispatcher

        app = Celery("myapp", broker="redis://localhost:6379/0")
        register_dispatcher(app, registry=FunctionRegistry(allowed_modules=["myapp"]))

    Args:
        app: The Celery app.
        name: Task name; must match the backend's ``dispatch_task``.
        registry: Where task names resolve. Give it an allowlist in production.
        task_options: Forwarded to ``@app.task`` (``max_retries``, ``acks_late``…).

    Returns:
        The registered Celery task.
    """
    task_options.setdefault("bind", True)

    # app is typed Any: celery must not be a hard dependency of this package, so
    # its decorator is untyped here.
    @app.task(name=name, **task_options)  # type: ignore[untyped-decorator]
    def _taskferry_dispatch(self: Any, message: TaskferryMessage) -> Any:
        policy = decode_retry(message)
        try:
            return execute_message(message, registry=registry)
        except Exception as exc:
            attempt = int(getattr(getattr(self, "request", None), "retries", 0) or 0) + 1
            if policy.should_retry(exc, attempt):
                # Celery owns the retry — a real re-queue with a real countdown —
                # rather than this function looping and holding a worker slot.
                # RetryOwner: exactly one layer retries, and here it is the engine.
                delay = policy.delay_for(attempt + 1)
                logger.info("requesting celery retry of attempt %d in %.1fs", attempt, delay)
                raise self.retry(exc=exc, countdown=delay) from exc
            raise

    return _taskferry_dispatch


__all__ = [
    "DEFAULT_DISPATCH_TASK",
    "build_message",
    "execute_message",
    "register_dispatcher",
]
