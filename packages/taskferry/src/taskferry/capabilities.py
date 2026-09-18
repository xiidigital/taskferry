"""What a backend can actually do.

Backends differ. Procrastinate can cancel a queued job; Cloud Tasks cannot. Cloud
Run Jobs can allocate a GPU; a thread pool cannot. Taskferry refuses to paper over
this: every backend advertises an immutable :class:`~taskferry.core.CapabilitySet`
and any operation requiring a capability it does not advertise raises
:class:`~taskferry.errors.UnsupportedCapability` instead of being silently faked
or approximated.

One enum is shared by all three execution kinds so that a router, a CLI or an
application can ask the same question of any backend::

    if Capability.CANCEL in backend.capabilities:
        ...

Domain-specific capability enums may still subclass
:class:`taskferry.core.Capability`; the values here are the portable vocabulary.
"""

from __future__ import annotations

from taskferry.core.capabilities import Capability as _CapabilityBase
from taskferry.core.capabilities import CapabilitySet


class Capability(_CapabilityBase):
    """The portable capability vocabulary shared by every Taskferry backend."""

    # -- submission --------------------------------------------------------- #
    SUBMIT = "submit"
    """Accept a spec and return an :class:`~taskferry.execution.Execution`.
    Every backend has this; it is listed so capability sets are self-describing."""

    DELAY = "delay"
    """Honour ``TaskSpec.delay`` / ``run_at`` — deferred execution."""

    PRIORITY = "priority"
    """Honour ``TaskSpec.priority``."""

    DEDUPLICATION = "deduplication"
    """Honour ``idempotency_key`` by collapsing duplicate submissions."""

    # -- observation -------------------------------------------------------- #
    STATE = "state"
    """Report the state of a previously submitted execution via ``get()``."""

    RESULT = "result"
    """Return the value a successful execution produced."""

    LOGS = "logs"
    """Expose a log location for an execution."""

    PROGRESS = "progress"
    """Report incremental progress while an execution is running."""

    # -- control ------------------------------------------------------------ #
    CANCEL = "cancel"
    """Cancel a queued or running execution."""

    RETRY = "retry"
    """Apply a :class:`~taskferry.retry.RetryPolicy` natively."""

    TIMEOUT = "timeout"
    """Enforce a wall-clock timeout on an execution."""

    SCHEDULE = "schedule"
    """Register a recurring schedule (distinct from a one-off delay)."""

    # -- resources (jobs) ---------------------------------------------------- #
    PARALLELISM = "parallelism"
    """Run more than one task instance per submission (array/indexed jobs)."""

    CPU = "cpu"
    """Honour a per-execution CPU request."""

    MEMORY = "memory"
    """Honour a per-execution memory request."""

    GPU = "gpu"
    """Allocate GPUs to an execution."""

    ENVIRONMENT = "environment"
    """Inject environment variables into an execution."""

    # -- calling conventions -------------------------------------------------- #
    ASYNC_CALLABLE = "async_callable"
    """Execute ``async def`` callables natively."""


__all__ = ["Capability", "CapabilitySet"]
