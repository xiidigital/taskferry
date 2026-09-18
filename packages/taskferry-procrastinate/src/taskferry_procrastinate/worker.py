"""The worker side — one Procrastinate task that runs every Taskferry message.

```mermaid
sequenceDiagram
    participant PG as PostgreSQL
    participant W as procrastinate worker
    participant D as taskferry:dispatch
    participant REG as FunctionRegistry
    participant FN as your function

    PG->>W: reserve job
    W->>D: dispatch(message)
    D->>REG: resolve "package.module:function"
    REG-->>D: callable
    D->>FN: call(*args, **kwargs)
    FN-->>D: value (discarded — see below)
    D-->>W: done
    W->>PG: mark succeeded
```

Taskferry supplies **no worker**. You run ``procrastinate worker``; this module
only registers the function that worker calls. Reservation, concurrency,
shutdown, heartbeats and the listen/notify loop are Procrastinate's, and
rebuilding any of them here would be exactly the mistake this project is
organised to avoid.

Security
--------

The dispatcher resolves a task *name* that arrived from the queue. If untrusted
input can reach your queue, that resolution is an arbitrary-import primitive, so
pass a :class:`~taskferry.functions.FunctionRegistry` configured with an
allowlist::

    registry = FunctionRegistry(allowed_modules=["myapp.tasks"])
    register_dispatcher(app, registry=registry)

With no registry the dispatcher builds a permissive one — convenient in
development, and documented as something to tighten before production.

Return values
-------------

The dispatcher returns whatever the task returned, but Procrastinate does not
store it, and the backend does not advertise ``RESULT``. Write results where they
belong instead of expecting the queue to keep them.
"""

from __future__ import annotations

import logging
from typing import Any

from taskferry.envelope import execute_envelope
from taskferry.functions import FunctionRegistry
from taskferry.retry import RetryPolicy

from .message import TaskferryMessage, build_message, check_version, decode_retry

logger = logging.getLogger("taskferry.procrastinate")

DEFAULT_DISPATCH_TASK = "taskferry:dispatch"


def execute_message(
    message: TaskferryMessage,
    *,
    registry: FunctionRegistry | None = None,
) -> Any:
    """Resolve and run one Taskferry message. Pure of Procrastinate.

    Kept independent of the engine so it can be unit-tested with a plain dict,
    and reused by any other adapter that carries the same envelope.
    """
    check_version(message)
    return execute_envelope(message, registry=registry)


def register_dispatcher(
    app: Any,
    *,
    name: str = DEFAULT_DISPATCH_TASK,
    registry: FunctionRegistry | None = None,
    **task_options: Any,
) -> Any:
    """Register the Taskferry dispatcher on a Procrastinate ``App``.

    Call once, at import time, in the module your worker loads::

        app = App(connector=PsycopgConnector(...))
        register_dispatcher(app, registry=FunctionRegistry(allowed_modules=["myapp"]))

    Args:
        app: The Procrastinate ``App``.
        name: Task name. Must match the backend's ``dispatch_task``.
        registry: Where task names resolve. Give it an allowlist in production.
        task_options: Forwarded to ``@app.task`` (``pass_context``, ``retry``...).

    Returns:
        The registered Procrastinate task.
    """
    task_options.setdefault("pass_context", True)

    # The App is deliberately typed Any: procrastinate must not be a hard
    # dependency of this package, so its decorator is untyped here.
    @app.task(name=name, **task_options)  # type: ignore[untyped-decorator]
    def _taskferry_dispatch(context: Any = None, *, message: TaskferryMessage) -> Any:
        policy = decode_retry(message)
        try:
            return execute_message(message, registry=registry)
        except Exception as exc:
            attempt = _attempt_number(context)
            if policy.should_retry(exc, attempt):
                _request_engine_retry(policy, attempt, exc)
            raise

    return _taskferry_dispatch


def _attempt_number(context: Any) -> int:
    """1-based attempt count, read from the Procrastinate job context."""
    job = getattr(context, "job", None)
    attempts = getattr(job, "attempts", None)
    return int(attempts) + 1 if isinstance(attempts, int) else 1


def _request_engine_retry(policy: RetryPolicy, attempt: int, exc: Exception) -> None:
    """Ask Procrastinate to re-queue this job, with the delay the policy asks for.

    Raising Procrastinate's own retry exception means *Procrastinate* performs
    the retry — a real re-queue with a real schedule — rather than this function
    looping in the worker and holding a slot while it sleeps. That is the whole
    point of :class:`~taskferry.retry.RetryOwner`: exactly one layer retries, and
    here it is the engine.

    If the installed Procrastinate does not expose the exception (very old, or a
    future rename), the original error propagates and Procrastinate applies
    whatever retry strategy the task was registered with. Guessing would be
    worse than falling back to the engine's own default.
    """
    delay = policy.delay_for(attempt + 1)
    try:
        from procrastinate.exceptions import JobRetry
    except ImportError:  # pragma: no cover - depends on the installed version
        logger.debug("procrastinate.exceptions.JobRetry unavailable; leaving retry to the engine")
        return
    logger.info("requesting retry of attempt %d in %.1fs after %s", attempt, delay, exc)
    raise JobRetry(delay) from exc


__all__ = [
    "DEFAULT_DISPATCH_TASK",
    "build_message",
    "execute_message",
    "register_dispatcher",
]
