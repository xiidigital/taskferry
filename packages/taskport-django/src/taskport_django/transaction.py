"""Enqueue after the transaction commits — the classic Django task bug, fixed.

```mermaid
sequenceDiagram
    participant V as view
    participant DB as PostgreSQL
    participant TP as Taskport
    participant W as worker

    Note over V,W: without on_commit — the race
    V->>DB: BEGIN; INSERT order
    V->>TP: submit(process_order, id)
    TP->>W: enqueue
    W->>DB: SELECT order  ❌ not committed yet
    V->>DB: COMMIT

    Note over V,W: with submit_on_commit
    V->>DB: BEGIN; INSERT order
    V->>TP: submit_on_commit(...)
    V->>DB: COMMIT
    TP->>W: enqueue
    W->>DB: SELECT order  ✓
```

A worker is usually faster than the rest of the request, so a task enqueued
mid-transaction routinely reaches a worker before the row it needs exists — and
it fails intermittently, under load, in production, which is the worst possible
place to discover it.

:func:`submit_on_commit` defers the submission to ``transaction.on_commit``. If
the transaction rolls back, nothing is enqueued at all, which is the other half
of the correctness argument.

This lives in the Django adapter because ``transaction.on_commit`` is Django's,
and the core cannot know about it. Other frameworks get the same shape from their
own after-commit hook wrapping ``runtime.submit``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.db import transaction

from taskport import ExecutionHandle, Taskport
from taskport.specs import AnySpec

from .config import get_runtime


def submit_on_commit(
    spec: AnySpec,
    *,
    runtime: Taskport | None = None,
    backend: str | None = None,
    using: str | None = None,
    on_submitted: Callable[[ExecutionHandle], None] | None = None,
) -> None:
    """Submit ``spec`` once the current transaction commits.

    Args:
        spec: What to run.
        runtime: Runtime to submit through. Defaults to the shared one.
        backend: Bypass routing and use this backend.
        using: Database alias whose transaction to hook.
        on_submitted: Called with the handle after submission. The handle cannot
            be returned — it does not exist yet when this function returns, and
            inventing a placeholder for it would be a lie with a ``.cancel()``
            method on it.

    Outside a transaction (``ATOMIC_REQUESTS`` off, no ``atomic`` block) Django
    runs the callback immediately, so this is always safe to call.
    """
    target = runtime if runtime is not None else get_runtime()

    def _submit() -> None:
        handle = target.submit(spec, backend=backend)
        if on_submitted is not None:
            on_submitted(handle)

    transaction.on_commit(_submit, using=using)


def task_on_commit(
    task: str | Callable[..., Any],
    /,
    *args: Any,
    runtime: Taskport | None = None,
    using: str | None = None,
    **kwargs: Any,
) -> None:
    """Shorthand for the common case: enqueue a task after commit.

    ::

        def create_order(request):
            with transaction.atomic():
                order = Order.objects.create(...)
                task_on_commit("myapp.tasks:process_order", order.id)
    """
    target = runtime if runtime is not None else get_runtime()
    ref = target.registry.reference(task)
    from taskport import TaskSpec

    submit_on_commit(
        TaskSpec(task=ref.path, args=args, kwargs=kwargs),
        runtime=target,
        using=using,
    )


__all__ = ["submit_on_commit", "task_on_commit"]
