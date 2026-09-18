"""The Taskferry runtime — one object an application holds and passes around.

```mermaid
flowchart TD
    APP["Application"]
    RT["Taskferry runtime"]
    ROUTER["Router"]
    REG["Backend registry<br/>(lazy)"]

    INLINE["inline backend"]
    TASK["task backend"]
    JOB["job backend"]

    APP -->|"runtime.tasks.submit(...)"| RT
    RT --> ROUTER
    ROUTER -->|"backend name"| REG
    REG --> INLINE
    REG --> TASK
    REG --> JOB
```

The runtime coordinates and does nothing else. It routes, it builds backends
lazily, it validates capabilities, it wraps results in handles, it fires hooks —
and then it gets out of the way. It never queues, never polls a broker, never
runs a worker loop. Those belong to the engines.

Three facades read the way the three primitives are actually used::

    runtime.inline.submit(add, 20, 22)                     # a callable, now
    runtime.tasks.submit("myapp.tasks:refresh", 42)        # a name, on an engine
    runtime.jobs.submit("build-cog", image="gdal:latest")   # a container, somewhere

Backends are built on first use and cached. Configuring a Cloud Run job backend
therefore costs a web process nothing until a job is actually routed to it, and
``import taskferry`` never reaches a provider SDK.

Thread safety
-------------

A runtime is safe to share across threads: backend construction is guarded by a
lock (so a backend is built exactly once), the router and config are immutable,
and the execution index is lock-protected. Whether a *backend* is thread-safe is
that backend's own promise; all built-ins are.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from types import TracebackType
from typing import Any, Self

from .capabilities import CapabilitySet
from .config import BackendConfig, TaskferryConfig
from .core.correlation import Correlation, ensure_correlation
from .core.errors import ConfigurationError
from .core.serialization import JsonSerializer, Serializer
from .core.typing import JSONValue
from .errors import ExecutionNotFound, RoutingError
from .execution import Execution, ExecutionId, ExecutionKind, ExecutionResult
from .functions import FunctionRef, FunctionRegistry
from .handle import ExecutionHandle
from .hooks import Hook, HookChain
from .plugins import build_backend
from .ports import BaseBackend, ExecutionBackend
from .retry import NO_RETRY, NO_TIMEOUT, RetryPolicy, TimeoutPolicy
from .router import Router
from .specs import AnySpec, BackendOptions, InlineSpec, JobSpec, Resources, TaskSpec


class Taskferry:
    """The portable execution layer.

    Args:
        config: Where backends, routes and defaults come from. Defaults to
            :meth:`TaskferryConfig.from_env`, so a Twelve-Factor deployment needs
            no code at all.
        backends: Pre-built backend instances, by name. Takes precedence over
            ``config.backends`` — this is how tests inject fakes and how an
            application that builds its own clients hands them over.
        router: Overrides the router derived from ``config``.
        serializer: Payload serializer handed to backends that need one.
        registry: Function registry used to resolve task names.
        hooks: Lifecycle observers. See :mod:`taskferry.hooks`.
    """

    def __init__(
        self,
        *,
        config: TaskferryConfig | None = None,
        backends: Mapping[str, ExecutionBackend] | None = None,
        router: Router | None = None,
        serializer: Serializer | None = None,
        registry: FunctionRegistry | None = None,
        hooks: Iterable[Hook] = (),
    ) -> None:
        self._config = config if config is not None else TaskferryConfig.from_env()
        self._router = router if router is not None else self._config.router()
        self._serializer = serializer if serializer is not None else JsonSerializer()
        self._registry = (
            registry
            if registry is not None
            else FunctionRegistry(
                allow_import=self._config.allow_import,
                allowed_modules=self._config.allowed_modules,
            )
        )
        self._hooks = HookChain(hooks)
        self._instances: dict[str, ExecutionBackend] = dict(backends or {})
        self._explicit: frozenset[str] = frozenset(self._instances)
        self._lock = threading.RLock()
        # Bounded id -> backend index so get()/cancel() can find the owner
        # without the caller having to remember it.
        self._index: OrderedDict[str, str] = OrderedDict()

        for name in self._instances:
            self._attach_hooks(self._instances[name])

        self.inline = InlineFacade(self)
        self.tasks = TaskFacade(self)
        self.jobs = JobFacade(self)

    # -- constructors ------------------------------------------------------- #
    @classmethod
    def local(cls, **kwargs: Any) -> Taskferry:
        """A runtime that needs no infrastructure whatsoever.

        Inline runs here, tasks run on a thread pool, jobs run as subprocesses::

            runtime = Taskferry.local()
            assert runtime.inline.submit(add, 20, 22).result().value == 42

        No Django, no PostgreSQL, no Redis, no cloud, no worker process.
        """
        kwargs.setdefault("config", TaskferryConfig.local())
        return cls(**kwargs)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, **kwargs: Any) -> Taskferry:
        """Build from ``TASKFERRY_*`` env vars. See :meth:`TaskferryConfig.from_env`."""
        kwargs.setdefault("config", TaskferryConfig.from_env(environ))
        return cls(**kwargs)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], **kwargs: Any) -> Taskferry:
        """Build from a plain mapping (TOML, YAML, Django settings, a literal dict)."""
        kwargs.setdefault("config", TaskferryConfig.from_mapping(data))
        return cls(**kwargs)

    # -- introspection ------------------------------------------------------ #
    @property
    def config(self) -> TaskferryConfig:
        return self._config

    @property
    def router(self) -> Router:
        return self._router

    @property
    def registry(self) -> FunctionRegistry:
        """Where task names are resolved. Register callables here for local runs."""
        return self._registry

    @property
    def serializer(self) -> Serializer:
        return self._serializer

    @property
    def hooks(self) -> HookChain:
        return self._hooks

    def backend_names(self) -> tuple[str, ...]:
        """Every configured backend name, built or not."""
        return tuple(sorted({*self._config.backends, *self._instances}))

    def backend(self, name: str) -> ExecutionBackend:
        """Return the named backend, building it on first use.

        Raises:
            ConfigurationError: if no backend is configured under ``name``.
        """
        with self._lock:
            existing = self._instances.get(name)
            if existing is not None:
                return existing
            definition: BackendConfig | None = self._config.backends.get(name)
            if definition is None:
                known = ", ".join(self.backend_names()) or "<none>"
                raise ConfigurationError(
                    f"no backend configured under {name!r} (configured: {known})"
                )
            instance = build_backend(definition.factory, definition.options)
            self._attach_hooks(instance)
            self._instances[name] = instance
            return instance

    def capabilities(self, name: str) -> CapabilitySet:
        """What the named backend can do. Builds it if necessary."""
        return self.backend(name).capabilities

    def describe(self) -> dict[str, Any]:
        """A JSON-shaped summary of the runtime, used by the CLI and by ``doctor``.

        Backends are *not* built to produce this — the point is to be able to
        inspect a configuration in an environment where a provider SDK may be
        missing.
        """
        return {
            "backends": {
                name: {
                    "factory": definition.factory,
                    "options": sorted(definition.options),
                    "built": name in self._instances,
                }
                for name, definition in sorted(self._config.backends.items())
            }
            | {
                name: {"factory": "<injected>", "options": [], "built": True}
                for name in sorted(self._explicit)
            },
            "routes": [route.describe() for route in self._router.routes],
            "defaults": {
                kind.value: backend for kind, backend in sorted(self._router.defaults.items())
            },
        }

    # -- submission ---------------------------------------------------------- #
    def submit(
        self,
        spec: AnySpec,
        *,
        backend: str | None = None,
        idempotency_key: str | None = None,
    ) -> ExecutionHandle:
        """Route ``spec`` to a backend, submit it, and return a live handle.

        Args:
            spec: What to run.
            backend: Bypass routing and use this backend by name. For a CLI flag
                or a deliberate one-off; application code should route.
            idempotency_key: Convenience for ``spec.evolve(idempotency_key=...)``.
                Requires the target backend to advertise ``DEDUPLICATION``, and
                is **not** an exactly-once promise — see ADR-0010.

        Raises:
            RoutingError: no backend matched and the kind has no default.
            UnsupportedCapability: the chosen backend cannot honour the spec.
        """
        target, name, prepared = self._prepare(
            spec, backend=backend, idempotency_key=idempotency_key
        )
        execution = target.submit(prepared)
        self._track(execution.id, name)
        return ExecutionHandle(execution, target)

    def _prepare(
        self,
        spec: AnySpec,
        *,
        backend: str | None,
        idempotency_key: str | None,
    ) -> tuple[ExecutionBackend, str, AnySpec]:
        """Route and validate, without submitting.

        Shared verbatim by the sync runtime and by
        :class:`~taskferry.aio.AsyncTaskferry`, so the two surfaces cannot drift on
        routing, correlation or the kind check — the parts where a divergence
        would be silent and expensive.
        """
        if idempotency_key is not None:
            spec = spec.evolve(idempotency_key=idempotency_key)
        if spec.correlation is None:
            spec = spec.evolve(correlation=ensure_correlation())

        name = backend if backend is not None else self._router.resolve(spec)
        target = self.backend(name)
        if target.kind is not spec.kind:
            raise RoutingError(
                f"{name!r} is a {target.kind.value} backend but was asked to run a "
                f"{spec.kind.value} spec ({spec.name!r}); check the routes for "
                f"queue={spec.queue!r} profile={spec.profile!r}"
            )
        return target, name, spec

    # -- lookup ------------------------------------------------------------- #
    def get(
        self, execution_id: ExecutionId | str, *, backend: str | None = None
    ) -> ExecutionHandle:
        """Return a handle for a previously submitted execution.

        The owning backend is remembered from submission. Across a process
        boundary that memory is gone, so pass ``backend=`` — Taskferry does not
        broadcast a lookup to every configured engine, because polling a cloud
        API for an id it has never seen is slow, costly, and misleading.

        Raises:
            ExecutionNotFound: when the owner is unknown or the backend has no
                record of the id.
        """
        target = self._owner_backend(execution_id, backend)
        execution = target.get(ExecutionId(str(execution_id)))
        return ExecutionHandle(execution, target)

    def _owner_backend(
        self, execution_id: ExecutionId | str, backend: str | None
    ) -> ExecutionBackend:
        """The backend that owns ``execution_id``. Shared with the async surface."""
        key = str(execution_id)
        name = backend if backend is not None else self._owner_of(key)
        if name is None:
            raise ExecutionNotFound(
                f"{key!r} was not submitted by this runtime; pass backend='<name>' to say "
                f"which engine owns it (configured: {', '.join(self.backend_names()) or '<none>'})"
            )
        return self.backend(name)

    def cancel(self, execution_id: ExecutionId | str, *, backend: str | None = None) -> Execution:
        """Cancel an execution. Requires the owning backend to advertise ``CANCEL``."""
        return self.get(execution_id, backend=backend).cancel()

    def wait(
        self,
        execution_id: ExecutionId | str,
        *,
        timeout: float | None = None,
        backend: str | None = None,
    ) -> Execution:
        """Block until an execution is terminal, then return the final snapshot."""
        return self.get(execution_id, backend=backend).wait(timeout)

    def result(
        self,
        execution_id: ExecutionId | str,
        *,
        timeout: float | None = None,
        backend: str | None = None,
    ) -> ExecutionResult:
        """Return an execution's outcome, waiting up to ``timeout``."""
        return self.get(execution_id, backend=backend).result(timeout)

    # -- lifecycle ----------------------------------------------------------- #
    def close(self) -> None:
        """Release every backend's resources and forget them. Idempotent.

        Injected backends are closed too: passing one in hands the runtime
        ownership of it. Keep your own reference and skip ``close()`` if you need
        a different lifetime. A closed runtime rebuilds configured backends on
        the next submit; injected ones are gone, so it is not reusable.
        """
        with self._lock:
            instances = list(self._instances.values())
            self._instances.clear()
            self._index.clear()
        for instance in instances:
            closer = getattr(instance, "close", None)
            if callable(closer):
                closer()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<Taskferry backends={list(self.backend_names())}>"

    # -- internals ----------------------------------------------------------- #
    def _attach_hooks(self, backend: ExecutionBackend) -> None:
        """Give a backend the runtime's hook chain, if it can hold one."""
        if isinstance(backend, BaseBackend):
            backend.hooks = self._hooks

    def _track(self, execution_id: ExecutionId, backend: str) -> None:
        limit = self._config.max_tracked_executions
        if limit == 0:
            return
        with self._lock:
            self._index[str(execution_id)] = backend
            self._index.move_to_end(str(execution_id))
            while len(self._index) > limit:
                self._index.popitem(last=False)

    def _owner_of(self, execution_id: str) -> str | None:
        with self._lock:
            return self._index.get(execution_id)


class _Facade:
    """Shared plumbing for the three primitive facades."""

    __slots__ = ("_runtime",)

    def __init__(self, runtime: Taskferry) -> None:
        self._runtime = runtime

    @property
    def backend_name(self) -> str | None:
        """The default backend for this kind, or ``None`` when unset."""
        return self._runtime.router.defaults.get(self._kind)

    @property
    def _kind(self) -> ExecutionKind:  # pragma: no cover - overridden
        raise NotImplementedError

    def submit_spec(self, spec: AnySpec, *, backend: str | None = None) -> ExecutionHandle:
        """Submit an already-built spec. The escape hatch from the shorthand."""
        return self._runtime.submit(spec, backend=backend)


class InlineFacade(_Facade):
    """``runtime.inline`` — run a callable right now, in this process."""

    __slots__ = ()

    @property
    def _kind(self) -> ExecutionKind:
        return ExecutionKind.INLINE

    def submit(
        self,
        func: Callable[..., Any],
        /,
        *args: Any,
        backend: str | None = None,
        name: str = "",
        queue: str = "default",
        retry: RetryPolicy = NO_RETRY,
        labels: Mapping[str, str] | None = None,
        correlation: Correlation | None = None,
        **kwargs: Any,
    ) -> ExecutionHandle:
        """Run ``func(*args, **kwargs)`` immediately and return a finished handle.

        The one primitive that takes a live callable: nothing crosses a process
        boundary, so there is nothing to serialize and no importable name to
        demand. Lambdas, closures and notebook functions all work.

        ``async def`` callables are awaited. Arguments are passed through
        untouched — no JSON validation — because they are not going anywhere.
        """
        spec = InlineSpec(
            func=func,
            args=args,
            kwargs=kwargs,
            name=name,
            queue=queue,
            retry=retry,
            labels=labels or {},
            correlation=correlation,
        )
        return self._runtime.submit(spec, backend=backend)

    def run(self, func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """Run ``func`` and return its value directly, raising on failure.

        Sugar over ``submit(...).result().value`` for scripts and tests.
        """
        return self.submit(func, *args, **kwargs).result().value


class TaskFacade(_Facade):
    """``runtime.tasks`` — hand a named function to a task engine."""

    __slots__ = ()

    @property
    def _kind(self) -> ExecutionKind:
        return ExecutionKind.TASK

    def submit(
        self,
        task: str | Callable[..., Any] | FunctionRef,
        /,
        *args: JSONValue,
        backend: str | None = None,
        queue: str = "default",
        priority: int = 0,
        delay: timedelta | float | None = None,
        run_at: datetime | None = None,
        retry: RetryPolicy = NO_RETRY,
        timeout: TimeoutPolicy = NO_TIMEOUT,
        idempotency_key: str | None = None,
        labels: Mapping[str, str] | None = None,
        backend_options: BackendOptions | Mapping[str, Any] | None = None,
        correlation: Correlation | None = None,
        **kwargs: JSONValue,
    ) -> ExecutionHandle:
        """Enqueue ``task`` for background execution.

        ``task`` may be the portable ``"package.module:function"`` string, or the
        callable itself — in which case its importable name is derived and it is
        registered locally, so the same code works whether the engine runs it
        here or on a worker three availability zones away. Lambdas and closures
        are rejected: a worker could never find them.

        Arguments must be JSON-shaped; they cross a process boundary. Pass an id,
        not an ORM instance.
        """
        ref = self._runtime.registry.reference(task)
        spec = TaskSpec(
            task=ref.path,
            args=args,
            kwargs=kwargs,
            queue=queue,
            priority=priority,
            delay=_as_timedelta(delay),
            run_at=run_at,
            retry=retry,
            timeout=timeout,
            idempotency_key=idempotency_key,
            labels=labels or {},
            backend_options=_as_backend_options(backend_options),
            correlation=correlation,
        )
        return self._runtime.submit(spec, backend=backend)


class JobFacade(_Facade):
    """``runtime.jobs`` — run an isolated workload to completion."""

    __slots__ = ()

    @property
    def _kind(self) -> ExecutionKind:
        return ExecutionKind.JOB

    def submit(
        self,
        job: str,
        /,
        *,
        backend: str | None = None,
        image: str | None = None,
        command: Sequence[str] = (),
        args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        resources: Resources | None = None,
        profile: str = "default",
        parallelism: int = 1,
        working_dir: str | None = None,
        retry: RetryPolicy = NO_RETRY,
        timeout: TimeoutPolicy | float | None = None,
        idempotency_key: str | None = None,
        labels: Mapping[str, str] | None = None,
        backend_options: BackendOptions | Mapping[str, Any] | None = None,
        correlation: Correlation | None = None,
    ) -> ExecutionHandle:
        """Submit an isolated workload — a container or a process.

        ``profile`` is the routing key for jobs the way ``queue`` is for tasks:
        ``profile="gpu"`` says what the work needs, and the deployment's routes
        decide whether that means a Kubernetes GPU node pool or a local process.
        """
        spec = JobSpec(
            job=job,
            image=image,
            command=command,
            args=args,
            env=env or {},
            resources=resources if resources is not None else Resources(),
            profile=profile,
            parallelism=parallelism,
            working_dir=working_dir,
            retry=retry,
            timeout=_as_timeout(timeout),
            idempotency_key=idempotency_key,
            labels=labels or {},
            backend_options=_as_backend_options(backend_options),
            correlation=correlation,
        )
        return self._runtime.submit(spec, backend=backend)


def _as_timedelta(value: timedelta | float | None) -> timedelta | None:
    if value is None or isinstance(value, timedelta):
        return value
    return timedelta(seconds=float(value))


def _as_timeout(value: TimeoutPolicy | float | None) -> TimeoutPolicy:
    if value is None:
        return NO_TIMEOUT
    if isinstance(value, TimeoutPolicy):
        return value
    return TimeoutPolicy(seconds=float(value))


def _as_backend_options(value: BackendOptions | Mapping[str, Any] | None) -> BackendOptions:
    if value is None:
        return BackendOptions()
    if isinstance(value, BackendOptions):
        return value
    return BackendOptions(dict(value))


__all__ = [
    "InlineFacade",
    "JobFacade",
    "TaskFacade",
    "Taskferry",
]
