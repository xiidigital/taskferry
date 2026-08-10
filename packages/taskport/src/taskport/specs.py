"""What to execute — the portable request objects.

```mermaid
flowchart TD
    ES["ExecutionSpec<br/>(shared: name · queue · profile · retry · timeout · metadata)"]
    IS["InlineSpec<br/>a callable, right now"]
    TS["TaskSpec<br/>a named function, on a task engine"]
    JS["JobSpec<br/>a container/process, on a batch runtime"]

    ES --> IS
    ES --> TS
    ES --> JS
```

Two rules shape every field here.

**Only genuinely portable things get a field.** ``queue``, ``priority``,
``retry``, ``timeout``, ``image``, ``resources`` mean something on every engine
that has the corresponding capability. ``procrastinate_lock``,
``celery_acks_late``, ``cloud_tasks_dispatch_deadline`` and
``kubernetes_node_selector`` do not, and are therefore *not* fields — they go
into :class:`~taskport.core.config.ProviderOptions`, namespaced by backend:

    TaskSpec(
        task="myapp.tasks.refresh_metadata",
        queue="metadata",
        backend_options=BackendOptions({"procrastinate": {"lock": "meta-42"}}),
    )

An adapter reads only its own namespace and ignores the rest, so one spec travels
unchanged from Procrastinate to Cloud Tasks and only the ignored extras differ.

**Every spec declares what it needs.** ``required_capabilities()`` turns the
fields that were actually set into the capabilities a backend must advertise. The
base backend classes check that before submitting, so asking a thread pool for a
GPU fails loudly at submit time rather than quietly producing a CPU run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any

from taskport.core.config import ProviderOptions
from taskport.core.correlation import Correlation
from taskport.core.serialization import ensure_json_serializable
from taskport.core.typing import JSONObject, JSONValue

from .capabilities import Capability
from .execution import ExecutionKind
from .functions import FunctionRef
from .retry import NO_RETRY, NO_TIMEOUT, RetryPolicy, TimeoutPolicy

BackendOptions = ProviderOptions
"""Backend-specific escape hatch, namespaced by backend name.

Aliased from :class:`taskport.core.config.ProviderOptions` — same object, a name
that reads correctly next to :class:`TaskSpec`."""


@dataclass(frozen=True, slots=True)
class Resources:
    """Compute a job asks for.

    ``cpu`` and ``memory`` use Kubernetes quantity syntax (``"1000m"``,
    ``"512Mi"``) because every batch runtime can translate from it and it is
    unambiguous about units. ``gpu`` is a count; ``gpu_type`` is the accelerator
    name when the runtime needs one (``"nvidia-tesla-t4"``).
    """

    cpu: str | None = None
    memory: str | None = None
    gpu: int = 0
    gpu_type: str | None = None

    def __post_init__(self) -> None:
        if self.gpu < 0:
            raise ValueError("gpu count must be >= 0")
        if self.gpu_type and not self.gpu:
            raise ValueError("gpu_type was set but gpu count is 0")

    @property
    def is_empty(self) -> bool:
        return not (self.cpu or self.memory or self.gpu)


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionSpec:
    """Fields common to every kind of execution.

    Attributes:
        name: Human-readable label. Defaults to something sensible per subclass.
        queue: Logical queue/lane. The primary routing key for tasks.
        profile: Logical resource profile (``"heavy"``, ``"gpu"``). The primary
            routing key for jobs. Never a provider name — that is the router's
            job to decide.
        retry: Portable retry intent. See :mod:`taskport.retry`.
        timeout: Portable wall-clock timeout intent.
        idempotency_key: Caller-supplied deduplication key. Backends advertising
            ``DEDUPLICATION`` collapse duplicate submissions carrying the same
            key. Taskport makes **no** exactly-once promise; this is a tool for
            building idempotency, not a guarantee. See ADR-0010.
        correlation: Correlation to propagate. Defaults to the ambient one at
            submit time when left unset.
        labels: Arbitrary metadata forwarded to the engine where supported.
        backend_options: Per-backend escape hatch. See the module docstring.
    """

    name: str = ""
    queue: str = "default"
    profile: str = "default"
    retry: RetryPolicy = NO_RETRY
    timeout: TimeoutPolicy = NO_TIMEOUT
    idempotency_key: str | None = None
    correlation: Correlation | None = None
    labels: Mapping[str, str] = field(default_factory=dict)
    backend_options: BackendOptions = field(default_factory=BackendOptions)

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))

    @property
    def kind(self) -> ExecutionKind:  # pragma: no cover - overridden everywhere
        raise NotImplementedError

    def required_capabilities(self) -> frozenset[Capability]:
        """Capabilities a backend must advertise to honour this spec faithfully."""
        required: set[Capability] = {Capability.SUBMIT}
        if self.retry.enabled and self.retry.owner.value == "backend":
            required.add(Capability.RETRY)
        if self.timeout.enabled:
            required.add(Capability.TIMEOUT)
        if self.idempotency_key is not None:
            required.add(Capability.DEDUPLICATION)
        return frozenset(required)

    def evolve(self, **changes: Any) -> Any:
        """Return a modified copy. Specs are immutable; this is how you adjust one."""
        return replace(self, **changes)

    def options_for(self, backend: str) -> JSONObject:
        """Read this spec's options for one backend (empty when none were set)."""
        return self.backend_options.for_provider(backend)


@dataclass(frozen=True, slots=True, kw_only=True)
class InlineSpec(ExecutionSpec):
    """Run a callable right now, in this process.

    The one spec that carries a live Python object: there is no process boundary
    to cross, so there is nothing to serialize and no reason to demand an
    importable name. That is exactly why inline works in a notebook, in a test,
    or on a lambda.

    Attributes:
        func: The callable to run. Sync or ``async def``.
        args: Positional arguments.
        kwargs: Keyword arguments.
    """

    func: Callable[..., Any]
    args: tuple[Any, ...] = ()
    kwargs: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Explicit base call: @dataclass(slots=True) rebuilds the class, which
        # invalidates the zero-argument super() cell in these methods.
        ExecutionSpec.__post_init__(self)
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "kwargs", MappingProxyType(dict(self.kwargs)))
        if not callable(self.func):
            raise TypeError(f"InlineSpec.func must be callable, got {type(self.func).__name__}")
        if not self.name:
            object.__setattr__(self, "name", getattr(self.func, "__qualname__", repr(self.func)))

    @property
    def kind(self) -> ExecutionKind:
        return ExecutionKind.INLINE


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskSpec(ExecutionSpec):
    """Run a named function on a task engine.

    Attributes:
        task: Portable function reference, ``"package.module:function"``.
        args: Positional arguments. Must be JSON-shaped — they cross a process
            boundary.
        kwargs: Keyword arguments. Must be JSON-shaped.
        priority: Engine priority. Higher runs sooner where supported; the
            absolute scale is engine-specific, only the ordering is portable.
        delay: Wait this long before the task becomes eligible.
        run_at: Absolute time before which the task must not run. Mutually
            exclusive with :attr:`delay`.

    Arguments are validated as JSON-serializable at construction, so a bad
    payload fails in the caller's stack trace rather than on a worker an hour
    later.
    """

    task: str
    args: tuple[JSONValue, ...] = ()
    kwargs: Mapping[str, JSONValue] = field(default_factory=dict)
    priority: int = 0
    delay: timedelta | None = None
    run_at: datetime | None = None

    def __post_init__(self) -> None:
        # Explicit base call: @dataclass(slots=True) rebuilds the class, which
        # invalidates the zero-argument super() cell in these methods.
        ExecutionSpec.__post_init__(self)
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "kwargs", MappingProxyType(dict(self.kwargs)))
        if not self.task:
            raise ValueError("TaskSpec.task is required")
        # Validate the reference eagerly: a malformed path is a programming
        # error, and finding it now beats finding it on a worker.
        FunctionRef.parse(self.task)
        # Same reasoning for the payload — a non-JSON argument must fail in the
        # caller's stack trace, not an hour later inside a worker.
        ensure_json_serializable(list(self.args))
        ensure_json_serializable(dict(self.kwargs))
        if self.delay is not None and self.run_at is not None:
            raise ValueError("set either TaskSpec.delay or TaskSpec.run_at, not both")
        if self.delay is not None and self.delay.total_seconds() < 0:
            raise ValueError("TaskSpec.delay must not be negative")
        if not self.name:
            object.__setattr__(self, "name", self.task)

    @property
    def kind(self) -> ExecutionKind:
        return ExecutionKind.TASK

    @property
    def function(self) -> FunctionRef:
        """The parsed function reference."""
        return FunctionRef.parse(self.task)

    @property
    def is_deferred(self) -> bool:
        return self.delay is not None or self.run_at is not None

    def scheduled_for(self, *, now: datetime | None = None) -> datetime | None:
        """Absolute time this task becomes eligible, or ``None`` if immediate.

        ``now`` is injectable so scheduling logic stays testable without
        freezing the clock globally.
        """
        if self.run_at is not None:
            return self.run_at
        if self.delay is None:
            return None
        from datetime import UTC

        base = now if now is not None else datetime.now(UTC)
        return base + self.delay

    def required_capabilities(self) -> frozenset[Capability]:
        required = set(ExecutionSpec.required_capabilities(self))
        if self.is_deferred:
            required.add(Capability.DELAY)
        if self.priority:
            required.add(Capability.PRIORITY)
        return frozenset(required)


@dataclass(frozen=True, slots=True, kw_only=True)
class JobSpec(ExecutionSpec):
    """Run an isolated workload — a container or a process — to completion.

    A Job is not a long Task. It has its own image, its own resource envelope and
    its own lifecycle, it does not share a worker with anything else, and it
    returns an exit code rather than a Python value. Collapsing the two would
    force every task engine to grow container semantics it does not have.

    Attributes:
        job: Job name. Also identifies the pre-declared provider resource for
            runtimes such as Cloud Run Jobs that run an existing Job definition.
        image: Container image. Required by container runtimes, ignored by the
            local subprocess backend.
        command: Full argv. The entrypoint for the local backend; the container
            entrypoint/args override for container runtimes.
        args: Extra arguments appended to :attr:`command`. Kept separate because
            several runtimes model entrypoint and args separately.
        env: Environment variables to inject.
        resources: CPU/memory/GPU request.
        parallelism: Number of task instances per submission (array jobs).
        working_dir: Working directory inside the container/process.
    """

    job: str
    image: str | None = None
    command: Sequence[str] = ()
    args: Sequence[str] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    resources: Resources = field(default_factory=Resources)
    parallelism: int = 1
    working_dir: str | None = None

    def __post_init__(self) -> None:
        # Explicit base call: @dataclass(slots=True) rebuilds the class, which
        # invalidates the zero-argument super() cell in these methods.
        ExecutionSpec.__post_init__(self)
        object.__setattr__(self, "command", tuple(self.command))
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))
        if not self.job:
            raise ValueError("JobSpec.job is required")
        if self.parallelism < 1:
            raise ValueError("JobSpec.parallelism must be >= 1")
        if not self.name:
            object.__setattr__(self, "name", self.job)

    @property
    def kind(self) -> ExecutionKind:
        return ExecutionKind.JOB

    @property
    def argv(self) -> tuple[str, ...]:
        """Full argv: :attr:`command` followed by :attr:`args`."""
        return (*self.command, *self.args)

    def required_capabilities(self) -> frozenset[Capability]:
        required = set(ExecutionSpec.required_capabilities(self))
        if self.parallelism > 1:
            required.add(Capability.PARALLELISM)
        if self.resources.cpu is not None:
            required.add(Capability.CPU)
        if self.resources.memory is not None:
            required.add(Capability.MEMORY)
        if self.resources.gpu > 0:
            required.add(Capability.GPU)
        if self.env:
            required.add(Capability.ENVIRONMENT)
        return frozenset(required)


AnySpec = InlineSpec | TaskSpec | JobSpec
"""Union of every spec kind — what :meth:`taskport.Taskport.submit` accepts."""


__all__ = [
    "AnySpec",
    "BackendOptions",
    "ExecutionSpec",
    "InlineSpec",
    "JobSpec",
    "Resources",
    "TaskSpec",
]
