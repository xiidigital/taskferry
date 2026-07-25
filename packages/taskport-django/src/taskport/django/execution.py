"""Consumer-side execution of a portable task message.

Both the Cloud Tasks push view and the SQS consumer funnel through
:func:`execute_task_message`, which resolves the task, restores correlation, and
runs it. Handlers must be idempotent — the transport is at-least-once (ADR-0008).
"""

from __future__ import annotations

from typing import Any

from taskport.core import (
    ATTR_CORRELATION_ID,
    ATTR_PROVIDER,
    SPAN_TASK_EXECUTE,
    Correlation,
    span,
    use_correlation,
)

from .message import TaskMessage, resolve_task


def execute_task_message(message: TaskMessage) -> Any:
    """Resolve and run the task described by ``message``.

    Raises:
        LookupError: if the referenced task cannot be resolved.
        NotImplementedError: for context-taking tasks (not yet supported by the
            push/consumer executor).
    """
    task = resolve_task(message)
    if task.takes_context:
        raise NotImplementedError(
            "context-taking tasks are not yet supported by the taskport executor"
        )
    args = list(message.get("args", []) or [])
    kwargs = dict(message.get("kwargs", {}) or {})
    corr_headers = message.get("correlation")
    correlation = Correlation.from_headers(corr_headers) if corr_headers else Correlation.start()
    with (
        use_correlation(correlation),
        span(
            SPAN_TASK_EXECUTE,
            {
                ATTR_PROVIDER: "taskport",
                ATTR_CORRELATION_ID: correlation.correlation_id,
            },
        ),
    ):
        return task.call(*args, **kwargs)


__all__ = ["execute_task_message"]
