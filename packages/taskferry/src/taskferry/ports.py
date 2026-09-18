"""The ports — the whole surface an adapter has to implement.

```mermaid
flowchart BT
    PRO["taskferry_procrastinate"]
    CT["taskferry_cloudtasks"]
    CR["taskferry_cloudrun"]
    DJ["taskferry_django"]

    TB["TaskBackend"]
    JB["JobBackend"]
    IB["InlineBackend"]

    CORE["taskferry<br/>specs · execution · runtime"]

    PRO --> TB
    CT --> TB
    CR --> JB
    DJ --> TB

    TB --> CORE
    JB --> CORE
    IB --> CORE
```

The surface is deliberately tiny: **submit** and **get**, plus **cancel** and
**result** whose availability is decided by the backend's advertised
capabilities rather than by whether a method happens to exist.

Why capabilities rather than method presence
--------------------------------------------

A duck-typed "does it have ``.cancel``?" check cannot express *"can cancel a
queued task but not a running one"*, cannot be inspected without instantiating
the backend, and cannot be reported by a CLI. So every backend implements the
full four-method surface, and :class:`~taskferry.capabilities.CapabilitySet` is
the single source of truth. Calling an unsupported operation raises
:class:`~taskferry.errors.UnsupportedCapability` — never a silent no-op and never
an emulation. See ADR-0005.

Adapters normally subclass :class:`BaseBackend`, which applies the capability
checks, the spec validation and the hook dispatch uniformly, leaving the adapter
with four underscore-prefixed methods that only talk to its engine.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from .capabilities import Capability, CapabilitySet
from .errors import ExecutionNotFound, TaskferryTimeoutError, UnsupportedCapability
from .execution import Execution, ExecutionId, ExecutionKind, ExecutionResult, ExecutionState
from .hooks import HookChain
from .specs import AnySpec, ExecutionSpec, InlineSpec, JobSpec, TaskSpec

DEFAULT_POLL_INTERVAL = 0.1
"""Seconds between polls in the generic :meth:`BaseBackend.wait` loop.

Backends with a native blocking wait should override ``_wait`` instead of
tuning this."""


@runtime_checkable
class ExecutionBackend(Protocol):
    """What every Taskferry backend, of every kind, provides."""

    @property
    def name(self) -> str:
        """Stable backend name used in routing, errors and ``Execution.backend``."""
        ...

    @property
    def kind(self) -> ExecutionKind:
        """The single execution kind this backend accepts."""
        ...

    @property
    def capabilities(self) -> CapabilitySet:
        """What this backend can actually do. Authoritative."""
        ...

    def submit(self, spec: ExecutionSpec) -> Execution:
        """Hand ``spec`` to the engine and return the resulting execution."""
        ...

    def get(self, execution_id: ExecutionId | str) -> Execution:
        """Return the current snapshot of a previously submitted execution.

        Accepts a Taskferry id or, where the adapter documents it, the engine's
        own id — so an operator holding a job id from a dashboard can look one up
        from a process that never submitted it.

        Requires :attr:`~taskferry.capabilities.Capability.STATE`.
        """
        ...

    def cancel(self, execution_id: ExecutionId | str) -> Execution:
        """Cancel an execution. Requires ``Capability.CANCEL``."""
        ...

    def result(
        self, execution_id: ExecutionId | str, *, timeout: float | None = None
    ) -> ExecutionResult:
        """Return the outcome, waiting up to ``timeout``. Requires ``Capability.RESULT``."""
        ...

    def wait(self, execution_id: ExecutionId | str, *, timeout: float | None = None) -> Execution:
        """Block until the execution is terminal. Requires ``Capability.STATE``."""
        ...

    # -- the async surface --------------------------------------------------- #
    # Every backend has one. :class:`BaseBackend` derives it from the sync
    # methods with ``asyncio.to_thread``, so an adapter gets a correct — never
    # loop-blocking — async path for free, and overrides only where its client
    # is genuinely async. See ADR-0015.

    async def asubmit(self, spec: ExecutionSpec) -> Execution: ...

    async def aget(self, execution_id: ExecutionId | str) -> Execution: ...

    async def acancel(self, execution_id: ExecutionId | str) -> Execution: ...

    async def aresult(
        self, execution_id: ExecutionId | str, *, timeout: float | None = None
    ) -> ExecutionResult: ...

    async def await_(
        self, execution_id: ExecutionId | str, *, timeout: float | None = None
    ) -> Execution:
        """Async ``wait``. Trailing underscore because ``await`` is a keyword."""
        ...


@runtime_checkable
class InlineBackend(ExecutionBackend, Protocol):
    """Runs an :class:`~taskferry.specs.InlineSpec` in the current process."""

    def submit(self, spec: ExecutionSpec) -> Execution: ...


@runtime_checkable
class TaskBackend(ExecutionBackend, Protocol):
    """Hands a :class:`~taskferry.specs.TaskSpec` to a task engine."""

    def submit(self, spec: ExecutionSpec) -> Execution: ...


@runtime_checkable
class JobBackend(ExecutionBackend, Protocol):
    """Hands a :class:`~taskferry.specs.JobSpec` to a batch runtime."""

    def submit(self, spec: ExecutionSpec) -> Execution: ...


_SPEC_FOR_KIND: dict[ExecutionKind, type[ExecutionSpec]] = {
    ExecutionKind.INLINE: InlineSpec,
    ExecutionKind.TASK: TaskSpec,
    ExecutionKind.JOB: JobSpec,
}


class BaseBackend(ABC):
    """Template base applying the rules every backend must follow.

    Subclasses declare :attr:`name`, :attr:`kind` and :attr:`capabilities`, then
    implement ``_submit`` and as many of ``_get`` / ``_cancel`` / ``_result`` as
    their capabilities promise. The public methods here guarantee, uniformly:

    * the spec is of the right kind for this backend;
    * every capability the spec requires is advertised — otherwise
      :class:`~taskferry.errors.UnsupportedCapability` before anything is sent;
    * operations the backend does not advertise raise rather than no-op;
    * hooks fire around submission.

    Instances are expected to be safe to share across threads once constructed;
    a backend holding mutable state must say so in its own docstring.
    """

    #: Hook chain invoked around submissions. The runtime injects its own.
    hooks: HookChain = HookChain()

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def kind(self) -> ExecutionKind: ...

    @property
    @abstractmethod
    def capabilities(self) -> CapabilitySet: ...

    # -- adapter hooks ------------------------------------------------------ #
    @abstractmethod
    def _submit(self, spec: ExecutionSpec) -> Execution:
        """Send the spec to the engine. Called only after validation."""

    def _get(self, execution_id: ExecutionId) -> Execution:
        raise ExecutionNotFound(f"{execution_id!r} is unknown to {self.name!r}", backend=self.name)

    def _cancel(self, execution_id: ExecutionId) -> Execution:  # pragma: no cover - overridden
        raise UnsupportedCapability(Capability.CANCEL.value, provider=self.name)

    def _result(
        self, execution_id: ExecutionId, *, timeout: float | None
    ) -> ExecutionResult:  # pragma: no cover - overridden
        raise UnsupportedCapability(Capability.RESULT.value, provider=self.name)

    def _wait(self, execution_id: ExecutionId, *, timeout: float | None) -> Execution:
        """Block until terminal. Default implementation polls ``_get``.

        Override when the engine offers a native blocking wait — polling is a
        fallback, not a design goal.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            execution = self.get(execution_id)
            if execution.is_terminal:
                return execution
            if deadline is not None and time.monotonic() >= deadline:
                raise TaskferryTimeoutError(
                    f"{execution_id!r} did not finish within {timeout}s "
                    f"(last state: {execution.state.value})"
                )
            time.sleep(DEFAULT_POLL_INTERVAL)

    # -- public surface ----------------------------------------------------- #
    def submit(self, spec: ExecutionSpec) -> Execution:
        self.validate(spec)
        self.hooks.before_submit(spec, self.name)
        try:
            execution = self._submit(spec)
        except Exception as exc:
            self.hooks.on_submit_error(spec, self.name, exc)
            raise
        self.hooks.after_submit(spec, execution)
        return execution

    def get(self, execution_id: ExecutionId | str) -> Execution:
        self.capabilities.require(Capability.STATE)
        return self._get(ExecutionId(str(execution_id)))

    def cancel(self, execution_id: ExecutionId | str) -> Execution:
        self.capabilities.require(Capability.CANCEL)
        execution = self._cancel(ExecutionId(str(execution_id)))
        self.hooks.on_cancel(execution)
        return execution

    def result(
        self, execution_id: ExecutionId | str, *, timeout: float | None = None
    ) -> ExecutionResult:
        self.capabilities.require(Capability.RESULT)
        return self._result(ExecutionId(str(execution_id)), timeout=timeout)

    def wait(self, execution_id: ExecutionId | str, *, timeout: float | None = None) -> Execution:
        """Block until the execution is terminal.

        Requires ``STATE``: without it there is nothing to poll, and pretending
        otherwise would mean returning a fabricated terminal state.
        """
        self.capabilities.require(Capability.STATE)
        return self._wait(ExecutionId(str(execution_id)), timeout=timeout)

    # -- the async surface ---------------------------------------------------- #
    # Derived from the sync methods by default. ``asyncio.to_thread`` runs the
    # blocking call on a worker thread, so the caller's event loop keeps
    # spinning — which is the whole point, and is why this is a real async path
    # rather than a cosmetic ``async def`` around a blocking call.
    #
    # An adapter whose client is natively async (aiobotocore, an async
    # Procrastinate connector) overrides these and skips the thread entirely.
    # Correctness does not depend on it doing so.

    async def asubmit(self, spec: ExecutionSpec) -> Execution:
        return await asyncio.to_thread(self.submit, spec)

    async def aget(self, execution_id: ExecutionId | str) -> Execution:
        return await asyncio.to_thread(self.get, execution_id)

    async def acancel(self, execution_id: ExecutionId | str) -> Execution:
        return await asyncio.to_thread(self.cancel, execution_id)

    async def aresult(
        self, execution_id: ExecutionId | str, *, timeout: float | None = None
    ) -> ExecutionResult:
        return await asyncio.to_thread(lambda: self.result(execution_id, timeout=timeout))

    async def await_(
        self, execution_id: ExecutionId | str, *, timeout: float | None = None
    ) -> Execution:
        """Async ``wait``. Trailing underscore because ``await`` is a keyword.

        The default polls on a worker thread. A backend whose engine offers a
        native async notification should override this — holding a thread for
        the lifetime of a long job is wasteful, even if it is correct.
        """
        return await asyncio.to_thread(lambda: self.wait(execution_id, timeout=timeout))

    # -- validation --------------------------------------------------------- #
    def validate(self, spec: ExecutionSpec) -> None:
        """Reject a spec this backend cannot honour, before anything is sent."""
        expected = _SPEC_FOR_KIND[self.kind]
        if not isinstance(spec, expected):
            raise TypeError(
                f"{self.name!r} is a {self.kind.value} backend and needs a "
                f"{expected.__name__}, got {type(spec).__name__}"
            )
        missing = self.capabilities.missing(spec.required_capabilities())
        if missing:
            names = ", ".join(sorted(str(c) for c in missing))
            raise UnsupportedCapability(names, provider=self.name)

    # -- convenience for adapters ------------------------------------------- #
    def close(self) -> None:  # noqa: B027 - an optional hook, deliberately not abstract
        """Release resources (pools, clients, connections). Idempotent.

        Not abstract: most adapters hold a stateless client and have nothing to
        release, and forcing every one of them to write an empty override would
        be noise that hides the few that genuinely need this.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} kind={self.kind.value!r}>"


def supports(backend: ExecutionBackend, capability: Capability) -> bool:
    """Whether ``backend`` advertises ``capability``. Reads better than ``in``."""
    return capability in backend.capabilities


def accepts(backend: ExecutionBackend, spec: AnySpec) -> bool:
    """Whether ``backend`` could honour ``spec`` without raising.

    Used by the router to skip a candidate backend rather than let submission
    fail, and by ``taskferry capabilities`` to explain why a route was chosen.
    """
    return backend.kind is spec.kind and backend.capabilities.supports_all(
        spec.required_capabilities()
    )


__all__ = [
    "DEFAULT_POLL_INTERVAL",
    "BaseBackend",
    "ExecutionBackend",
    "ExecutionState",
    "InlineBackend",
    "JobBackend",
    "TaskBackend",
    "accepts",
    "supports",
]
