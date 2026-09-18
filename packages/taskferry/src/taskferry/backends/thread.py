"""A task backend backed by a thread pool.

The reference `TaskBackend` implementation: enough to develop against, run tests
against and ship a single-process tool with, and small enough to read in one
sitting.

```mermaid
flowchart LR
    SPEC["TaskSpec"]
    B["ThreadTaskBackend"]
    Q["ThreadPoolExecutor"]
    REG["FunctionRegistry"]
    F["your function"]

    SPEC --> B --> Q
    B -->|"resolve 'pkg.mod:fn'"| REG --> F
    Q --> F
```

What it is not
--------------

It is not a queue. Work lives in this process's memory: nothing survives a
restart, nothing is visible to another process, and a crash loses every pending
task. Delivery is therefore **at-most-once**. When that is not good enough —
which is most of production — route the queue to Procrastinate or Cloud Tasks.
Taskferry exists precisely so that is a configuration change.

What it does support, honestly: state, results, cancellation of tasks that have
not started, deferred execution, in-process retries with real backoff, and
``async def`` task functions.

It does **not** advertise ``PRIORITY``. A ``ThreadPoolExecutor`` is strictly
FIFO, so accepting a priority and then ignoring it would be exactly the quiet
lie the capability model exists to prevent. A spec with a non-zero priority is
rejected here and routed to an engine that can honour it.
"""

from __future__ import annotations

import contextlib
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from ..capabilities import Capability, CapabilitySet
from ..core.correlation import use_correlation
from ..errors import ExecutionNotFound, TaskferryTimeoutError
from ..execution import (
    Execution,
    ExecutionId,
    ExecutionKind,
    ExecutionResult,
    ExecutionState,
    new_execution_id,
)
from ..functions import FunctionRegistry, is_async_callable
from ..ports import BaseBackend
from ..specs import ExecutionSpec, TaskSpec

THREAD_CAPABILITIES = frozenset(
    {
        Capability.SUBMIT,
        Capability.STATE,
        Capability.RESULT,
        Capability.CANCEL,
        Capability.DELAY,
        Capability.RETRY,
        Capability.ASYNC_CALLABLE,
    }
)


class _Entry:
    """Everything the backend knows about one submitted task."""

    __slots__ = ("execution", "future", "result", "spec")

    def __init__(self, execution: Execution, spec: TaskSpec) -> None:
        self.execution = execution
        self.spec = spec
        self.future: Future[Any] | None = None
        self.result: ExecutionResult | None = None


class ThreadTaskBackend(BaseBackend):
    """Runs tasks on a :class:`~concurrent.futures.ThreadPoolExecutor`.

    Args:
        max_workers: Pool size. ``None`` uses the interpreter default.
        registry: Where ``"package.module:function"`` names are resolved. Share
            the runtime's registry to pick up locally registered callables.
        history: How many finished executions to keep for ``get``/``result``.

    Thread-safe: all mutable state is guarded by one lock.
    """

    def __init__(
        self,
        *,
        max_workers: int | None = None,
        registry: FunctionRegistry | None = None,
        history: int = 1024,
    ) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="taskferry-task"
        )
        self._registry = registry if registry is not None else FunctionRegistry()
        self._history = history
        self._entries: dict[str, _Entry] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._closed = False

    @property
    def name(self) -> str:
        return "thread"

    @property
    def kind(self) -> ExecutionKind:
        return ExecutionKind.TASK

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(THREAD_CAPABILITIES, provider=self.name)

    @property
    def registry(self) -> FunctionRegistry:
        return self._registry

    # -- submission ---------------------------------------------------------- #
    def _submit(self, spec: ExecutionSpec) -> Execution:
        assert isinstance(spec, TaskSpec)
        if self._closed:
            from ..errors import SubmissionError

            raise SubmissionError("this ThreadTaskBackend is closed", backend=self.name)

        execution = Execution(
            id=new_execution_id(ExecutionKind.TASK),
            kind=ExecutionKind.TASK,
            backend=self.name,
            state=ExecutionState.QUEUED,
            name=spec.name,
            created_at=datetime.now(UTC),
            correlation=spec.correlation,
            metadata={"queue": spec.queue, "priority": str(spec.priority)},
        )
        entry = _Entry(execution, spec)
        with self._lock:
            self._entries[str(execution.id)] = entry
            self._remember_locked(str(execution.id))
        entry.future = self._pool.submit(self._run, str(execution.id))
        return execution

    # -- the worker ----------------------------------------------------------- #
    def _run(self, key: str) -> Any:
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:  # evicted or cancelled before we started
            return None
        spec = entry.spec

        self._wait_until_eligible(spec)
        if self._is_cancelled(key):
            return None

        self._transition(key, ExecutionState.RUNNING, started_at=datetime.now(UTC))
        with self._lock:
            running = self._entries[key].execution
        self.hooks.before_execute(running)

        try:
            func = self._registry.resolve(spec.task)
        except Exception as exc:
            # Resolution failure is a real outcome, not an internal error. Left
            # to propagate it would complete the future while the execution sat
            # in RUNNING forever, and every wait() on it would block until its
            # timeout with no explanation.
            self._finish(key, error=exc, attempt=1)
            return None

        attempt = 1
        bind: contextlib.AbstractContextManager[object] = (
            use_correlation(spec.correlation)
            if spec.correlation is not None
            else contextlib.nullcontext()
        )
        while True:
            try:
                with bind:
                    value = self._call(func, spec)
            except Exception as exc:
                if spec.retry.should_retry(exc, attempt):
                    self.hooks.on_retry(running, attempt + 1, exc)
                    delay = spec.retry.delay_for(attempt + 1)
                    if delay:
                        time.sleep(delay)
                    attempt += 1
                    continue
                self._finish(key, error=exc, attempt=attempt)
                return None
            self._finish(key, value=value, attempt=attempt)
            return value

    def _call(self, func: Any, spec: TaskSpec) -> Any:
        args = list(spec.args)
        kwargs = dict(spec.kwargs)
        if not is_async_callable(func):
            return func(*args, **kwargs)
        import asyncio

        return asyncio.run(func(*args, **kwargs))

    def _wait_until_eligible(self, spec: TaskSpec) -> None:
        """Honour ``delay``/``run_at`` by holding the worker thread.

        Sleeping a *pool* thread is real deferral, not a fake one: the task
        genuinely does not run before its time. It does occupy a worker while it
        waits, which is why a production deployment should route deferred work to
        an engine that can schedule properly.
        """
        eligible_at = spec.scheduled_for()
        if eligible_at is None:
            return
        remaining = (eligible_at - datetime.now(UTC)).total_seconds()
        while remaining > 0:
            time.sleep(min(remaining, 0.25))
            remaining = (eligible_at - datetime.now(UTC)).total_seconds()

    # -- state transitions ---------------------------------------------------- #
    def _transition(self, key: str, state: ExecutionState, **changes: Any) -> None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry.execution.state is ExecutionState.CANCELLED:
                return
            entry.execution = entry.execution.evolve(state=state, **changes)

    def _is_cancelled(self, key: str) -> bool:
        with self._lock:
            entry = self._entries.get(key)
            return entry is None or entry.execution.state is ExecutionState.CANCELLED

    def _finish(
        self,
        key: str,
        *,
        value: Any = None,
        error: BaseException | None = None,
        attempt: int,
    ) -> None:
        if error is None:
            result = ExecutionResult(value=value)
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
                state=state, finished_at=datetime.now(UTC), attempt=attempt, result=result
            )
            execution = entry.execution
        self.hooks.after_execute(execution, result)
        if error is None:
            self.hooks.on_success(execution, result)
        else:
            self.hooks.on_failure(execution, error)

    # -- observation ----------------------------------------------------------- #
    def _get(self, execution_id: ExecutionId) -> Execution:
        with self._lock:
            entry = self._entries.get(str(execution_id))
        if entry is None:
            raise ExecutionNotFound(
                f"task {execution_id!r} is not in this backend's {self._history}-entry history",
                backend=self.name,
            )
        return entry.execution

    def _result(self, execution_id: ExecutionId, *, timeout: float | None) -> ExecutionResult:
        self._wait(execution_id, timeout=timeout)
        with self._lock:
            entry = self._entries[str(execution_id)]
            result = entry.result
        if result is None:  # cancelled before it ran
            return ExecutionResult.cancelled()
        return result

    def _wait(self, execution_id: ExecutionId, *, timeout: float | None) -> Execution:
        entry_future = self._future_for(execution_id)
        if entry_future is not None:
            try:
                entry_future.result(timeout=timeout)
            except TimeoutError as exc:
                raise TaskferryTimeoutError(
                    f"task {execution_id!r} did not finish within {timeout}s"
                ) from exc
            except Exception:
                pass  # the failure is recorded on the execution, not raised here
        return self._get(execution_id)

    def _future_for(self, execution_id: ExecutionId) -> Future[Any] | None:
        with self._lock:
            entry = self._entries.get(str(execution_id))
        if entry is None:
            raise ExecutionNotFound(f"task {execution_id!r} is unknown", backend=self.name)
        return entry.future

    def _cancel(self, execution_id: ExecutionId) -> Execution:
        key = str(execution_id)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                raise ExecutionNotFound(f"task {execution_id!r} is unknown", backend=self.name)
            if entry.execution.is_terminal:
                return entry.execution
            future = entry.future
        # A task already running cannot be interrupted — Python has no safe way to
        # stop a thread mid-call. Cancelling only works before it starts, and the
        # returned state says which happened rather than claiming success.
        stopped = future.cancel() if future is not None else True
        with self._lock:
            entry = self._entries[key]
            if stopped or entry.execution.state is ExecutionState.QUEUED:
                entry.execution = entry.execution.evolve(
                    state=ExecutionState.CANCELLED, finished_at=datetime.now(UTC)
                )
            return entry.execution

    def _remember_locked(self, key: str) -> None:
        self._order.append(key)
        while len(self._order) > self._history:
            evicted = self._order.pop(0)
            if evicted != key:
                self._entries.pop(evicted, None)

    def close(self) -> None:
        self._closed = True
        self._pool.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._entries.clear()
            self._order.clear()


__all__ = ["THREAD_CAPABILITIES", "ThreadTaskBackend"]
