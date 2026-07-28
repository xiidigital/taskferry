"""Observability hooks — composable, explicit, never global.

Taskport deliberately has no global signal bus. A bus makes it impossible to
know which listeners will run, listeners registered by a library leak into every
consumer, and tests have to remember to tear down. Instead a :class:`Hook` is an
ordinary object you pass to the runtime::

    runtime = Taskport(config=config, hooks=[LoggingHook(), MetricsHook()])

```mermaid
sequenceDiagram
    participant App
    participant TP as Taskport
    participant H as Hooks
    participant B as Backend
    participant E as Engine

    App->>TP: submit(spec)
    TP->>H: before_submit(spec, backend)
    TP->>B: submit(spec)
    B->>E: enqueue
    E-->>B: execution
    B-->>TP: Execution
    TP->>H: after_submit(spec, execution)
    TP-->>App: ExecutionHandle
```

Every method has a no-op default, so a hook implements only what it cares about.
A hook that raises is *never* allowed to break the submission it observes — the
chain catches, reports through :func:`hook_error_handler`, and carries on. A
telemetry outage must not take the application down with it.

Ordering, precisely
-------------------

Hooks come in two families, and the guarantee is *per family*:

* **submit-side** — ``before_submit`` always precedes ``after_submit``;
* **execute-side** — ``before_execute`` precedes ``on_success``/``on_failure``,
  with ``on_retry`` in between for each re-attempt.

Across the two families there is **no ordering guarantee** whenever execution is
concurrent. A thread-pool worker can start before ``after_submit`` returns; a
Procrastinate worker runs in a different process, where ordering is not even a
meaningful question. The one exception is inline execution, where the work
happens inside ``submit`` and every execute-side hook therefore fires before
``after_submit``. A hook that needs to distinguish "submitted" from "finished"
should read :attr:`~taskport.execution.Execution.state`, not infer it from
arrival order.

Trace context propagation is separate: it rides on
:class:`~taskport.core.correlation.Correlation`, which adapters copy into engine
metadata. Hooks observe; correlation travels.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from typing import Protocol, runtime_checkable

from .execution import Execution, ExecutionResult
from .specs import ExecutionSpec

logger = logging.getLogger("taskport.hooks")

HookErrorHandler = Callable[[str, "Hook", BaseException], None]
"""Called when a hook method raises: ``(method_name, hook, exception)``."""


def _log_hook_error(method: str, hook: Hook, exc: BaseException) -> None:
    logger.warning("taskport hook %r failed in %s(): %s", type(hook).__name__, method, exc)


hook_error_handler: HookErrorHandler = _log_hook_error
"""Module-level policy for hook failures. Replace to re-raise in tests."""


@runtime_checkable
class Hook(Protocol):
    """Observe the lifecycle of executions. Every method is optional."""

    def before_submit(self, spec: ExecutionSpec, backend: str) -> None: ...

    def after_submit(self, spec: ExecutionSpec, execution: Execution) -> None: ...

    def on_submit_error(self, spec: ExecutionSpec, backend: str, exc: BaseException) -> None: ...

    def before_execute(self, execution: Execution) -> None: ...

    def after_execute(self, execution: Execution, result: ExecutionResult) -> None: ...

    def on_success(self, execution: Execution, result: ExecutionResult) -> None: ...

    def on_failure(self, execution: Execution, exc: BaseException) -> None: ...

    def on_cancel(self, execution: Execution) -> None: ...

    def on_retry(self, execution: Execution, attempt: int, exc: BaseException) -> None: ...


class BaseHook:
    """No-op implementation of every :class:`Hook` method. Subclass and override."""

    def before_submit(self, spec: ExecutionSpec, backend: str) -> None:
        """Called before a spec is handed to ``backend``."""

    def after_submit(self, spec: ExecutionSpec, execution: Execution) -> None:
        """Called once the backend has accepted the spec."""

    def on_submit_error(self, spec: ExecutionSpec, backend: str, exc: BaseException) -> None:
        """Called when submission itself failed. Nothing was enqueued."""

    def before_execute(self, execution: Execution) -> None:
        """Called by in-process backends immediately before running the work."""

    def after_execute(self, execution: Execution, result: ExecutionResult) -> None:
        """Called by in-process backends once the work finished, success or not."""

    def on_success(self, execution: Execution, result: ExecutionResult) -> None:
        """Called when an execution reached ``SUCCEEDED``."""

    def on_failure(self, execution: Execution, exc: BaseException) -> None:
        """Called when an execution failed with ``exc``."""

    def on_cancel(self, execution: Execution) -> None:
        """Called when an execution was cancelled through Taskport."""

    def on_retry(self, execution: Execution, attempt: int, exc: BaseException) -> None:
        """Called by in-process backends before re-attempting after ``exc``."""


class HookChain:
    """Fans one lifecycle event out to several hooks, in registration order.

    Immutable and safe to share across threads. A failing hook is reported and
    skipped; the remaining hooks still run.
    """

    __slots__ = ("_hooks",)

    def __init__(self, hooks: Iterable[Hook] = ()) -> None:
        self._hooks: tuple[Hook, ...] = tuple(hooks)

    @property
    def hooks(self) -> Sequence[Hook]:
        return self._hooks

    def __len__(self) -> int:
        return len(self._hooks)

    def __bool__(self) -> bool:
        return bool(self._hooks)

    def __repr__(self) -> str:
        names = ", ".join(type(h).__name__ for h in self._hooks)
        return f"HookChain({names})"

    def with_hook(self, hook: Hook) -> HookChain:
        """Return a new chain with ``hook`` appended. The original is unchanged."""
        return HookChain((*self._hooks, hook))

    def _dispatch(self, method: str, *args: object) -> None:
        for hook in self._hooks:
            handler = getattr(hook, method, None)
            if handler is None:
                continue
            try:
                handler(*args)
            except Exception as exc:  # never let telemetry break the workload
                hook_error_handler(method, hook, exc)

    def before_submit(self, spec: ExecutionSpec, backend: str) -> None:
        self._dispatch("before_submit", spec, backend)

    def after_submit(self, spec: ExecutionSpec, execution: Execution) -> None:
        self._dispatch("after_submit", spec, execution)

    def on_submit_error(self, spec: ExecutionSpec, backend: str, exc: BaseException) -> None:
        self._dispatch("on_submit_error", spec, backend, exc)

    def before_execute(self, execution: Execution) -> None:
        self._dispatch("before_execute", execution)

    def after_execute(self, execution: Execution, result: ExecutionResult) -> None:
        self._dispatch("after_execute", execution, result)

    def on_success(self, execution: Execution, result: ExecutionResult) -> None:
        self._dispatch("on_success", execution, result)

    def on_failure(self, execution: Execution, exc: BaseException) -> None:
        self._dispatch("on_failure", execution, exc)

    def on_cancel(self, execution: Execution) -> None:
        self._dispatch("on_cancel", execution)

    def on_retry(self, execution: Execution, attempt: int, exc: BaseException) -> None:
        self._dispatch("on_retry", execution, attempt, exc)


class LoggingHook(BaseHook):
    """Logs every lifecycle event. Useful on its own and as a worked example."""

    def __init__(self, logger_name: str = "taskport", level: int = logging.INFO) -> None:
        self._log = logging.getLogger(logger_name)
        self._level = level

    def after_submit(self, spec: ExecutionSpec, execution: Execution) -> None:
        self._log.log(
            self._level,
            "submitted %s %s to %s (%s)",
            execution.kind.value,
            execution.name,
            execution.backend,
            execution.id,
        )

    def on_submit_error(self, spec: ExecutionSpec, backend: str, exc: BaseException) -> None:
        self._log.warning("submission of %s to %s failed: %s", spec.name, backend, exc)

    def on_failure(self, execution: Execution, exc: BaseException) -> None:
        self._log.warning("%s %s failed: %s", execution.kind.value, execution.id, exc)

    def on_retry(self, execution: Execution, attempt: int, exc: BaseException) -> None:
        self._log.info("retrying %s (attempt %d) after %s", execution.id, attempt, exc)

    def on_cancel(self, execution: Execution) -> None:
        self._log.info("cancelled %s %s", execution.kind.value, execution.id)


__all__ = [
    "BaseHook",
    "Hook",
    "HookChain",
    "HookErrorHandler",
    "LoggingHook",
    "hook_error_handler",
]
