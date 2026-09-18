"""ExecutionHandle — the live reference you actually hold.

:class:`~taskferry.execution.Execution` is a *snapshot*: immutable, cheap, exactly
what the backend knew at one moment. An :class:`ExecutionHandle` is the *live*
reference: it remembers which backend owns the execution and can go ask again.

```mermaid
flowchart LR
    H["ExecutionHandle<br/>id · backend ref · cached snapshot"]
    B["Backend"]
    E["Execution snapshot"]

    H -->|"refresh() / status()"| B
    B --> E
    E --> H
```

Operations the backend does not advertise raise
:class:`~taskferry.errors.UnsupportedCapability` — a handle never fakes a cancel
and never invents a result.

Thread safety
-------------

A handle caches the most recent snapshot behind a lock, so concurrent
``refresh()`` calls from several threads are safe and always leave a consistent
snapshot. The handle is bound to a live backend object, so it does **not** cross
a process boundary; to follow an execution from elsewhere, carry
:attr:`ExecutionHandle.id` and call ``runtime.get(id)`` there.
"""

from __future__ import annotations

import contextlib
import threading
from datetime import datetime

from .capabilities import Capability, CapabilitySet
from .errors import ExecutionError, TaskferryTimeoutError, UnsupportedCapability
from .execution import Execution, ExecutionId, ExecutionKind, ExecutionResult, ExecutionState
from .ports import ExecutionBackend


class ExecutionHandle:
    """A refreshable reference to one execution on one backend."""

    __slots__ = ("_backend", "_execution", "_lock")

    def __init__(self, execution: Execution, backend: ExecutionBackend) -> None:
        self._execution = execution
        self._backend = backend
        self._lock = threading.Lock()

    # -- identity ----------------------------------------------------------- #
    @property
    def id(self) -> ExecutionId:
        return self._execution.id

    @property
    def kind(self) -> ExecutionKind:
        return self._execution.kind

    @property
    def name(self) -> str:
        return self._execution.name

    @property
    def backend(self) -> str:
        """Name of the backend that owns this execution."""
        return self._backend.name

    @property
    def capabilities(self) -> CapabilitySet:
        """What the owning backend can do — ask before calling ``cancel``."""
        return self._backend.capabilities

    @property
    def external_id(self) -> str | None:
        """The engine's own id for this execution, when it reported one."""
        return self._execution.external_id

    @property
    def created_at(self) -> datetime | None:
        return self._execution.created_at

    # -- current snapshot ---------------------------------------------------- #
    @property
    def execution(self) -> Execution:
        """The most recent snapshot. Does **not** contact the backend."""
        with self._lock:
            return self._execution

    @property
    def state(self) -> ExecutionState:
        """State as of the last snapshot. Call :meth:`status` to re-read."""
        return self.execution.state

    @property
    def done(self) -> bool:
        """Whether the last snapshot was terminal. Does not contact the backend."""
        return self.execution.is_terminal

    def __repr__(self) -> str:
        snapshot = self.execution
        return (
            f"<ExecutionHandle {snapshot.kind.value} {snapshot.id} "
            f"backend={self.backend!r} state={snapshot.state.value!r}>"
        )

    # -- live operations ------------------------------------------------------ #
    def refresh(self) -> Execution:
        """Re-read the execution from the backend and cache the new snapshot.

        A terminal snapshot is never re-read: terminal states do not change, and
        polling a finished execution wastes an engine round-trip.
        """
        with self._lock:
            if self._execution.is_terminal:
                return self._execution
        fresh = self._backend.get(self.id)
        with self._lock:
            self._execution = fresh
        return fresh

    def status(self) -> ExecutionState:
        """Refresh and return the current state."""
        return self.refresh().state

    def wait(self, timeout: float | None = None) -> Execution:
        """Block until the execution is terminal, then return the final snapshot.

        Raises:
            TaskferryTimeoutError: if ``timeout`` elapses first.
            UnsupportedCapability: if the backend cannot report state.
        """
        with self._lock:
            if self._execution.is_terminal:
                return self._execution
        final = self._backend.wait(self.id, timeout=timeout)
        with self._lock:
            self._execution = final
        return final

    def result(self, timeout: float | None = None) -> ExecutionResult:
        """Return the outcome, waiting up to ``timeout`` for it.

        Raises:
            ExecutionError: if the execution failed.
            TaskferryTimeoutError: if it did not finish in time.
            UnsupportedCapability: if the backend cannot return results.
        """
        outcome = self._backend.result(self.id, timeout=timeout)
        if self._backend.capabilities.supports(Capability.STATE):
            # Refreshing is a courtesy so the handle reflects the final state; a
            # slow backend must not turn a successful result() into a failure.
            with contextlib.suppress(TaskferryTimeoutError):
                self.refresh()
        if outcome.error is not None:
            raise ExecutionError(
                f"{self.kind.value} {self.id} failed: {outcome.error}",
                backend=self.backend,
                cause_repr=outcome.traceback or outcome.error,
            )
        return outcome

    def value(self, timeout: float | None = None) -> object:
        """The value the execution produced — :meth:`result` without the wrapper."""
        return self.result(timeout).value

    def cancel(self) -> Execution:
        """Cancel the execution.

        Raises:
            UnsupportedCapability: if the backend cannot cancel. Taskferry does
                not emulate cancellation; an emulated cancel that leaves work
                running is worse than a clear refusal.
        """
        if not self._backend.capabilities.supports(Capability.CANCEL):
            raise UnsupportedCapability(Capability.CANCEL.value, provider=self.backend)
        cancelled = self._backend.cancel(self.id)
        with self._lock:
            self._execution = cancelled
        return cancelled


__all__ = ["ExecutionHandle"]
