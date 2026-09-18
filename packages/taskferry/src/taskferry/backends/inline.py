"""Inline execution — run it here, run it now.

The simplest backend and the one that makes Taskferry usable before any
infrastructure exists. ``submit`` runs the callable synchronously and returns an
already-terminal execution:

```mermaid
sequenceDiagram
    participant App
    participant TP as Taskferry
    participant IB as InlineExecutionBackend
    participant F as your function

    App->>TP: inline.submit(add, 20, 22)
    TP->>IB: submit(InlineSpec)
    IB->>F: add(20, 22)
    F-->>IB: 42
    IB-->>TP: Execution(SUCCEEDED, result=42)
    TP-->>App: ExecutionHandle (already done)
```

What it honestly does **not** do
--------------------------------

* **No timeout.** Nothing can preempt a synchronous Python call in the calling
  thread. ``TIMEOUT`` is not advertised, so a spec asking for one is rejected
  rather than silently ignored.
* **No cancel.** By the time ``submit`` returns, the work is over.
* **No delay.** Sleeping the caller's thread to honour ``delay`` would be a
  denial of service dressed up as a feature.

Retries *are* real here: this backend is the engine, so it owns the retry and
applies the :class:`~taskferry.retry.RetryPolicy` itself, backoff included.

Async callables
---------------

An ``async def`` is awaited with :func:`asyncio.run`. If a loop is already
running in this thread, the coroutine is run on a private loop in a worker
thread — blocking the caller either way, because ``submit`` is synchronous by
contract. Inside async code, use ``await runtime.inline.submit_async(...)``
instead of blocking the loop.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import time
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

from ..capabilities import Capability, CapabilitySet
from ..core.correlation import use_correlation
from ..execution import (
    Execution,
    ExecutionId,
    ExecutionKind,
    ExecutionResult,
    ExecutionState,
    new_execution_id,
)
from ..functions import is_async_callable
from ..ports import BaseBackend
from ..retry import RetryPolicy
from ..specs import ExecutionSpec, InlineSpec

INLINE_CAPABILITIES = frozenset(
    {
        Capability.SUBMIT,
        Capability.STATE,
        Capability.RESULT,
        Capability.RETRY,
        Capability.ASYNC_CALLABLE,
    }
)


class InlineExecutionBackend(BaseBackend):
    """Runs :class:`~taskferry.specs.InlineSpec` callables in the calling thread.

    Finished executions are kept in a bounded ring so ``get()``/``result()`` work
    for a while after submission. The bound matters: a long-running process that
    submits inline work in a loop must not accumulate results forever. Once an id
    ages out, ``get()`` raises :class:`~taskferry.errors.ExecutionNotFound` — the
    handle returned by ``submit`` still carries the terminal snapshot and the
    result, so the common path never depends on the cache.
    """

    def __init__(self, *, history: int = 1024) -> None:
        if history < 0:
            raise ValueError("history must be >= 0")
        self._history = history
        self._executions: dict[str, Execution] = {}
        self._results: dict[str, ExecutionResult] = {}
        self._order: list[str] = []

    @property
    def name(self) -> str:
        return "inline"

    @property
    def kind(self) -> ExecutionKind:
        return ExecutionKind.INLINE

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(INLINE_CAPABILITIES, provider=self.name)

    # -- submission ---------------------------------------------------------- #
    def _submit(self, spec: ExecutionSpec) -> Execution:
        assert isinstance(spec, InlineSpec)  # guaranteed by BaseBackend.validate
        execution_id = new_execution_id(ExecutionKind.INLINE)
        started = datetime.now(UTC)
        base = Execution(
            id=execution_id,
            kind=ExecutionKind.INLINE,
            backend=self.name,
            state=ExecutionState.RUNNING,
            name=spec.name,
            created_at=started,
            started_at=started,
            correlation=spec.correlation,
        )
        self.hooks.before_execute(base)

        value, error, attempts = self._call_with_retries(spec, base)
        finished = datetime.now(UTC)

        if error is None:
            result = ExecutionResult(value=value)
            state = ExecutionState.SUCCEEDED
        else:
            result = ExecutionResult.from_exception(error)
            state = ExecutionState.FAILED

        execution = base.evolve(
            state=state,
            finished_at=finished,
            attempt=attempts,
            result=result,
        )
        self.hooks.after_execute(execution, result)
        if error is None:
            self.hooks.on_success(execution, result)
        else:
            self.hooks.on_failure(execution, error)
        self._remember(execution, result)
        return execution

    def _call_with_retries(
        self, spec: InlineSpec, execution: Execution
    ) -> tuple[Any, BaseException | None, int]:
        """Run the callable, applying the retry policy. This backend owns retries."""
        policy = spec.retry
        attempt = 1
        bind = (
            use_correlation(spec.correlation)
            if spec.correlation is not None
            else contextlib.nullcontext()
        )
        while True:
            try:
                with bind:
                    return self._call(spec), None, attempt
            except Exception as exc:
                if not policy.should_retry(exc, attempt):
                    return None, exc, attempt
                self.hooks.on_retry(execution, attempt + 1, exc)
                _sleep(policy, attempt + 1)
                attempt += 1

    def _call(self, spec: InlineSpec) -> Any:
        if not is_async_callable(spec.func):
            return spec.func(*spec.args, **spec.kwargs)
        coro: Coroutine[Any, Any, Any] = spec.func(*spec.args, **spec.kwargs)
        return _run_coroutine(coro)

    # -- observation ---------------------------------------------------------- #
    def _get(self, execution_id: ExecutionId) -> Execution:
        execution = self._executions.get(str(execution_id))
        if execution is None:
            from ..errors import ExecutionNotFound

            raise ExecutionNotFound(
                f"inline execution {execution_id!r} is not in this backend's "
                f"{self._history}-entry history",
                backend=self.name,
            )
        return execution

    def _result(self, execution_id: ExecutionId, *, timeout: float | None) -> ExecutionResult:
        self._get(execution_id)  # raises ExecutionNotFound with a good message
        return self._results[str(execution_id)]

    def _remember(self, execution: Execution, result: ExecutionResult) -> None:
        if self._history == 0:
            return
        key = str(execution.id)
        self._executions[key] = execution
        self._results[key] = result
        self._order.append(key)
        while len(self._order) > self._history:
            evicted = self._order.pop(0)
            self._executions.pop(evicted, None)
            self._results.pop(evicted, None)

    def close(self) -> None:
        self._executions.clear()
        self._results.clear()
        self._order.clear()


def _sleep(policy: RetryPolicy, attempt: int) -> None:
    delay = policy.delay_for(attempt)
    if delay > 0:
        time.sleep(delay)


def _run_coroutine(coro: Coroutine[Any, Any, Any]) -> Any:
    """Await ``coro`` from synchronous code, whether or not a loop is running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # A loop is already running in this thread; asyncio.run() would raise. Run
    # the coroutine on its own loop in a worker thread instead. This blocks the
    # caller, which is the documented contract of the synchronous submit().
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


__all__ = ["INLINE_CAPABILITIES", "InlineExecutionBackend"]
