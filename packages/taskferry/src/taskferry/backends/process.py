"""A task backend backed by a process pool.

Same shape as :mod:`taskferry.backends.thread`, one significant difference: the
work runs in a *different interpreter*, so it is a genuine rehearsal for what a
real worker deployment does to your code.

```mermaid
flowchart LR
    P["parent process"]
    C1["worker process 1"]
    C2["worker process 2"]

    P -->|"'pkg.mod:fn' + JSON args"| C1
    P -->|"'pkg.mod:fn' + JSON args"| C2
    C1 -->|"JSON result"| P
    C2 -->|"JSON result"| P
```

Nothing but the function *name* and JSON arguments crosses the boundary — the
same contract Procrastinate and Cloud Tasks impose. Code that works here works
on a worker; code that relied on a closure, a module-level global mutated at
import time, or a live ORM object fails here, at development time, where it is
cheap to find. That is the whole reason this backend exists alongside the thread
pool.

Because the child imports the function by name, ``__main__``-defined functions
and lambdas cannot be used —
:class:`~taskferry.functions.FunctionRef` rejects them at submit time with an
explanation rather than letting the pool fail cryptically.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ProcessPoolExecutor
from datetime import UTC, datetime
from typing import Any

from ..capabilities import Capability, CapabilitySet
from ..core.typing import JSONValue
from ..errors import ExecutionNotFound, SubmissionError, TaskferryTimeoutError
from ..execution import (
    Execution,
    ExecutionId,
    ExecutionKind,
    ExecutionResult,
    ExecutionState,
    new_execution_id,
)
from ..functions import FunctionRef, is_async_callable
from ..ports import BaseBackend
from ..retry import RetryPolicy
from ..specs import ExecutionSpec, TaskSpec

PROCESS_CAPABILITIES = frozenset(
    {
        Capability.SUBMIT,
        Capability.STATE,
        Capability.RESULT,
        Capability.CANCEL,
        Capability.RETRY,
        Capability.ASYNC_CALLABLE,
    }
)


def _execute_in_child(
    task: str,
    args: list[JSONValue],
    kwargs: dict[str, JSONValue],
    policy: RetryPolicy,
) -> Any:
    """Entry point that runs in the worker process.

    Module-level and picklable on purpose: a ``ProcessPoolExecutor`` can only
    dispatch to something importable, which is the same constraint every real
    task engine imposes.

    Retries happen here, in the child, so an attempt that dies takes only its own
    process time and the parent is not blocked coordinating them.
    """
    import asyncio

    func = FunctionRef.parse(task).resolve()
    attempt = 1
    while True:
        try:
            if is_async_callable(func):
                return asyncio.run(func(*args, **kwargs))
            return func(*args, **kwargs)
        except Exception as exc:
            if not policy.should_retry(exc, attempt):
                raise
            attempt += 1
            delay = policy.delay_for(attempt)
            if delay:
                import time

                time.sleep(delay)


class _Entry:
    __slots__ = ("execution", "future", "result")

    def __init__(self, execution: Execution) -> None:
        self.execution = execution
        self.future: Future[Any] | None = None
        self.result: ExecutionResult | None = None


class ProcessTaskBackend(BaseBackend):
    """Runs tasks on a :class:`~concurrent.futures.ProcessPoolExecutor`.

    Args:
        max_workers: Pool size. ``None`` uses the interpreter default.
        history: How many finished executions to keep for ``get``/``result``.

    Like the thread backend this is **not** durable: pending work dies with the
    parent. It does not advertise ``DELAY`` (nothing here schedules) or
    ``PRIORITY`` (the pool is FIFO).

    The pool is created lazily on first submit, so constructing this backend —
    which the runtime does eagerly when it is configured — never forks.
    """

    def __init__(self, *, max_workers: int | None = None, history: int = 1024) -> None:
        self._max_workers = max_workers
        self._history = history
        self._pool: ProcessPoolExecutor | None = None
        self._entries: dict[str, _Entry] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._closed = False

    @property
    def name(self) -> str:
        return "process"

    @property
    def kind(self) -> ExecutionKind:
        return ExecutionKind.TASK

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(PROCESS_CAPABILITIES, provider=self.name)

    def _ensure_pool(self) -> ProcessPoolExecutor:
        with self._lock:
            if self._closed:
                raise SubmissionError("this ProcessTaskBackend is closed", backend=self.name)
            if self._pool is None:
                self._pool = ProcessPoolExecutor(max_workers=self._max_workers)
            return self._pool

    # -- submission ------------------------------------------------------------ #
    def _submit(self, spec: ExecutionSpec) -> Execution:
        assert isinstance(spec, TaskSpec)
        pool = self._ensure_pool()
        execution = Execution(
            id=new_execution_id(ExecutionKind.TASK),
            kind=ExecutionKind.TASK,
            backend=self.name,
            state=ExecutionState.QUEUED,
            name=spec.name,
            created_at=datetime.now(UTC),
            correlation=spec.correlation,
            metadata={"queue": spec.queue},
        )
        key = str(execution.id)
        entry = _Entry(execution)
        with self._lock:
            self._entries[key] = entry
            self._remember_locked(key)
        try:
            future = pool.submit(
                _execute_in_child,
                spec.task,
                list(spec.args),
                dict(spec.kwargs),
                spec.retry,
            )
        except Exception as exc:
            raise SubmissionError(
                f"could not dispatch {spec.task!r} to the process pool: {exc}", backend=self.name
            ) from exc
        entry.future = future
        future.add_done_callback(lambda fut: self._settle(key, fut))
        return execution

    def _settle(self, key: str, future: Future[Any]) -> None:
        """Record the outcome once the child reports back."""
        if future.cancelled():
            self._set_state(key, ExecutionState.CANCELLED)
            return
        error = future.exception()
        if error is None:
            result = ExecutionResult(value=future.result())
            state = ExecutionState.SUCCEEDED
        else:
            result = ExecutionResult.from_exception(error)
            state = ExecutionState.FAILED
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return
            entry.result = result
            entry.execution = entry.execution.evolve(
                state=state, finished_at=datetime.now(UTC), result=result
            )
            execution = entry.execution
        self.hooks.after_execute(execution, result)
        if error is None:
            self.hooks.on_success(execution, result)
        else:
            self.hooks.on_failure(execution, error)

    def _set_state(self, key: str, state: ExecutionState) -> None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return
            entry.execution = entry.execution.evolve(state=state, finished_at=datetime.now(UTC))

    # -- observation ------------------------------------------------------------ #
    def _entry(self, execution_id: ExecutionId) -> _Entry:
        with self._lock:
            entry = self._entries.get(str(execution_id))
        if entry is None:
            raise ExecutionNotFound(
                f"task {execution_id!r} is not in this backend's {self._history}-entry history",
                backend=self.name,
            )
        return entry

    def _get(self, execution_id: ExecutionId) -> Execution:
        entry = self._entry(execution_id)
        # RUNNING is not observable across the pool boundary: a future is either
        # pending or done, so QUEUED stands until the child reports.
        if entry.future is not None and entry.future.running():
            with self._lock:
                if entry.execution.state is ExecutionState.QUEUED:
                    entry.execution = entry.execution.evolve(state=ExecutionState.RUNNING)
        return entry.execution

    def _wait(self, execution_id: ExecutionId, *, timeout: float | None) -> Execution:
        entry = self._entry(execution_id)
        if entry.future is not None:
            try:
                entry.future.result(timeout=timeout)
            except TimeoutError as exc:
                raise TaskferryTimeoutError(
                    f"task {execution_id!r} did not finish within {timeout}s"
                ) from exc
            except Exception:
                pass  # recorded on the execution by _settle
        return self._get(execution_id)

    def _result(self, execution_id: ExecutionId, *, timeout: float | None) -> ExecutionResult:
        self._wait(execution_id, timeout=timeout)
        result = self._entry(execution_id).result
        if result is None:
            return ExecutionResult.cancelled()
        return result

    def _cancel(self, execution_id: ExecutionId) -> Execution:
        entry = self._entry(execution_id)
        if entry.execution.is_terminal:
            return entry.execution
        # Only a task that has not been picked up can be stopped; a running child
        # is left alone rather than killed mid-write.
        if entry.future is not None and entry.future.cancel():
            self._set_state(str(execution_id), ExecutionState.CANCELLED)
        return self._entry(execution_id).execution

    def _remember_locked(self, key: str) -> None:
        self._order.append(key)
        while len(self._order) > self._history:
            evicted = self._order.pop(0)
            if evicted != key:
                self._entries.pop(evicted, None)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            pool, self._pool = self._pool, None
            self._entries.clear()
            self._order.clear()
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)


__all__ = ["PROCESS_CAPABILITIES", "ProcessTaskBackend"]
