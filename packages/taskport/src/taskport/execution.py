"""Execution — the portable identity of one unit of work in flight.

Every backend, of every kind, describes what it did in these terms. An
:class:`Execution` is an immutable *snapshot*: what the backend knew at the
moment it was asked. An :class:`~taskport.handle.ExecutionHandle` (see
``taskport.handle``) is the live, refreshable reference built on top of it.

State machine
-------------

```mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> QUEUED
    PENDING --> RUNNING
    QUEUED --> RUNNING
    QUEUED --> CANCELLED
    RUNNING --> SUCCEEDED
    RUNNING --> FAILED
    RUNNING --> CANCELLED
    RUNNING --> TIMED_OUT
    FAILED --> QUEUED: retry (engine-owned)
    SUCCEEDED --> [*]
    CANCELLED --> [*]
    TIMED_OUT --> [*]
```

Not every backend can observe every transition. An inline execution goes straight
from ``PENDING`` to a terminal state; a fire-and-forget push queue may only ever
report ``QUEUED`` and then ``UNKNOWN``. Adapters map what the provider actually
reports and use :data:`ExecutionState.UNKNOWN` rather than guessing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from taskport.core.correlation import Correlation
from taskport.core.ids import TaskportId, new_id
from taskport.core.provider import ProviderMetadata

ExecutionId = TaskportId
"""A Taskport-owned execution id. Never the provider's id — that lives in
:attr:`Execution.external_id`, so one execution stays followable across systems."""


class ExecutionKind(StrEnum):
    """Which execution primitive produced this execution."""

    INLINE = "inline"
    TASK = "task"
    JOB = "job"


class ExecutionState(StrEnum):
    """Portable lifecycle state. See the diagram in the module docstring."""

    PENDING = "pending"
    """Accepted by Taskport, not yet acknowledged by the engine."""

    QUEUED = "queued"
    """Accepted by the engine, waiting for a worker or a slot."""

    RUNNING = "running"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"

    UNKNOWN = "unknown"
    """The backend cannot determine the state. Not an error and not terminal —
    a later poll may resolve it."""

    @property
    def is_terminal(self) -> bool:
        """Whether no further transition is expected (barring an engine retry)."""
        return self in _TERMINAL

    @property
    def is_successful(self) -> bool:
        return self is ExecutionState.SUCCEEDED


_TERMINAL = frozenset(
    {
        ExecutionState.SUCCEEDED,
        ExecutionState.FAILED,
        ExecutionState.CANCELLED,
        ExecutionState.TIMED_OUT,
    }
)

#: The transitions Taskport considers legal. Adapters are not forced through
#: this table (providers report what they report), but ``Execution.transitions_to``
#: lets tests and diagnostics flag a mapping that cannot be right.
_ALLOWED: Mapping[ExecutionState, frozenset[ExecutionState]] = MappingProxyType(
    {
        ExecutionState.PENDING: frozenset(
            {ExecutionState.QUEUED, ExecutionState.RUNNING, ExecutionState.UNKNOWN} | _TERMINAL
        ),
        # SUCCEEDED is reachable directly from QUEUED, and not as an edge case:
        # a worker that finishes between the submit and the first poll means the
        # observer never sees RUNNING at all. Every real engine does this
        # constantly. A transition table that forbids it would flag correct
        # adapters as broken.
        ExecutionState.QUEUED: frozenset(
            {ExecutionState.RUNNING, ExecutionState.UNKNOWN} | _TERMINAL
        ),
        ExecutionState.RUNNING: frozenset(
            {ExecutionState.UNKNOWN} | _TERMINAL,
        ),
        # An engine-owned retry legitimately moves a failed execution back into
        # the queue; see ADR-0011 on retry ownership.
        ExecutionState.FAILED: frozenset({ExecutionState.QUEUED, ExecutionState.RUNNING}),
        ExecutionState.TIMED_OUT: frozenset({ExecutionState.QUEUED}),
        ExecutionState.SUCCEEDED: frozenset(),
        ExecutionState.CANCELLED: frozenset(),
        ExecutionState.UNKNOWN: frozenset(ExecutionState),
    }
)


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """The outcome of a finished execution.

    ``value`` is only meaningful when the backend advertises
    :attr:`~taskport.capabilities.Capability.RESULT`; otherwise it stays ``None``
    and callers must not read anything into that.
    """

    value: Any = None
    error: str | None = None
    error_type: str | None = None
    traceback: str | None = None
    exit_code: int | None = None
    logs_uri: str | None = None

    @classmethod
    def from_exception(cls, exc: BaseException) -> ExecutionResult:
        """Build a failed result from a raised exception.

        Captures the message, qualified type name and formatted traceback the
        same way every in-process backend needs, so the construction lives here
        once instead of being copied into each one.
        """
        import traceback as _traceback

        return cls(
            error=str(exc) or type(exc).__name__,
            error_type=type(exc).__qualname__,
            traceback="".join(_traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )

    @classmethod
    def cancelled(cls) -> ExecutionResult:
        """The placeholder result for an execution cancelled before it produced one."""
        return cls(error="execution was cancelled", error_type="ExecutionCancelled")


@dataclass(frozen=True, slots=True)
class Execution:
    """An immutable snapshot of one unit of work.

    Attributes:
        id: Taskport-owned id, stable for the life of the execution.
        kind: Which primitive produced it.
        backend: Name of the backend that owns it (``"local"``, ``"procrastinate"``).
        state: Portable lifecycle state at snapshot time.
        name: Human-readable name — the task path or the job name.
        created_at: When Taskport accepted the submission.
        started_at: When the engine began running it, if reported.
        finished_at: When it reached a terminal state, if reported.
        external_id: The engine's own id (Procrastinate job id, Cloud Run
            execution name, Cloud Tasks task name).
        attempt: 1-based attempt counter, when the engine reports one.
        result: Outcome, populated once terminal and if the backend can supply it.
        correlation: Correlation propagated from the spec.
        provider_metadata: Region/resource/labels for the underlying resource.
        metadata: Free-form backend annotations. JSON-shaped by convention.
    """

    id: ExecutionId
    kind: ExecutionKind
    backend: str
    state: ExecutionState
    name: str = ""
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    external_id: str | None = None
    attempt: int = 1
    result: ExecutionResult | None = None
    correlation: Correlation | None = None
    provider_metadata: ProviderMetadata | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def is_terminal(self) -> bool:
        return self.state.is_terminal

    @property
    def is_successful(self) -> bool:
        return self.state.is_successful

    def transitions_to(self, state: ExecutionState) -> bool:
        """Whether moving from this snapshot's state to ``state`` is legal.

        Used by the contract suite to catch adapters that map provider states
        onto impossible transitions (``SUCCEEDED`` → ``RUNNING``, say).
        """
        return state is self.state or state in _ALLOWED[self.state]

    def evolve(self, **changes: Any) -> Execution:
        """Return a copy with ``changes`` applied. The original is never mutated."""
        return replace(self, **changes)


def new_execution_id(kind: ExecutionKind = ExecutionKind.TASK) -> ExecutionId:
    """Mint a fresh execution id prefixed with the kind (``task_``, ``job_``)."""
    return new_id(kind.value)


__all__ = [
    "Execution",
    "ExecutionId",
    "ExecutionKind",
    "ExecutionResult",
    "ExecutionState",
    "new_execution_id",
]
