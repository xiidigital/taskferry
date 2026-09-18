"""The async surface: `AsyncTaskferry`, with the same method names.

```python
from taskferry import AsyncTaskferry

runtime = AsyncTaskferry.local()

handle = await runtime.tasks.submit("myapp.tasks:send_email", 42)
execution = await handle.wait(30)
value = await handle.value()
```

Two runtimes, one vocabulary
----------------------------

`AsyncTaskferry` mirrors :class:`~taskferry.runtime.Taskferry` method for method,
with the *same names*. That is deliberate, and it is why there is a second class
rather than an `asubmit`/`aget`/`acancel` prefix soup bolted onto the first:

* `await` is a keyword, so a prefixed `wait` has nowhere to go — `await_()`,
  `awaited()` and `wait_async()` are all worse than `wait()`;
* porting a module between the two surfaces becomes adding or removing `await`,
  not rewriting every call site;
* it is the convention Python has settled on — `httpx.Client` /
  `httpx.AsyncClient`, `redis.Redis` / `redis.asyncio.Redis`.

The adapter-facing port keeps the `a`-prefixed names (`asubmit`, `aget`,
`acancel`, `aresult`, `await_`), because there the two surfaces sit on **one**
object and have to be told apart. Adapter authors read those; application
developers do not. See ADR-0015.

```mermaid
flowchart TD
    APP["async application"]
    ART["AsyncTaskferry"]
    RT["Taskferry<br/>routing · config · backends"]
    BE["backend.asubmit()"]

    APP -->|"await tasks.submit(...)"| ART
    ART -->|"shares everything"| RT
    ART --> BE
```

What is shared, and what is not
-------------------------------

An `AsyncTaskferry` **wraps** a sync runtime rather than duplicating it. Routing,
configuration, the backend cache, the execution index, hooks and the function
registry are the same objects — so a process can hold one runtime and expose both
surfaces without two connection pools, two backend instances or two views of
which execution went where:

```python
runtime = Taskferry.local()
aio = AsyncTaskferry(runtime)          # same backends, same everything

handle = runtime.tasks.submit(fn, 1)             # from a management command
handle = await aio.tasks.submit(fn, 1)           # from an async view
```

Blocking, honestly
------------------

A backend's async path defaults to `asyncio.to_thread` around its sync path (see
:class:`~taskferry.ports.BaseBackend`). That is a real async path — the caller's
event loop keeps running — not an `async def` painted over a blocking call. An
adapter with a natively async client overrides it and skips the thread; nothing
here depends on whether it has.

The one thing this cannot fix is an `async def` **task function** running on an
in-process backend: the thread pool runs it with `asyncio.run` on its own loop,
because that is what a worker in another process would do too.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from types import TracebackType
from typing import Any, Self

from .capabilities import Capability, CapabilitySet
from .config import TaskferryConfig
from .core.correlation import Correlation
from .core.typing import JSONValue
from .errors import ExecutionError, UnsupportedCapability
from .execution import Execution, ExecutionId, ExecutionKind, ExecutionResult, ExecutionState
from .functions import FunctionRef
from .ports import ExecutionBackend
from .retry import NO_RETRY, NO_TIMEOUT, RetryPolicy, TimeoutPolicy
from .router import Router
from .runtime import Taskferry, _as_backend_options, _as_timedelta, _as_timeout
from .specs import AnySpec, BackendOptions, InlineSpec, JobSpec, Resources, TaskSpec


class AsyncExecutionHandle:
    """The async twin of :class:`~taskferry.handle.ExecutionHandle`.

    Same properties, same method names; the methods that touch the backend are
    coroutines. Cheap, non-blocking reads (:attr:`id`, :attr:`state`,
    :attr:`done`) stay plain attributes — making them coroutines would force an
    `await` on data already in memory.
    """

    __slots__ = ("_backend", "_execution", "_lock")

    def __init__(self, execution: Execution, backend: ExecutionBackend) -> None:
        self._execution = execution
        self._backend = backend
        self._lock = asyncio.Lock()

    # -- identity (no I/O, no await) ------------------------------------------ #
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
        return self._backend.name

    @property
    def capabilities(self) -> CapabilitySet:
        return self._backend.capabilities

    @property
    def external_id(self) -> str | None:
        return self._execution.external_id

    @property
    def execution(self) -> Execution:
        """The most recent snapshot. Does not contact the backend."""
        return self._execution

    @property
    def state(self) -> ExecutionState:
        return self._execution.state

    @property
    def done(self) -> bool:
        return self._execution.is_terminal

    def __repr__(self) -> str:
        return (
            f"<AsyncExecutionHandle {self._execution.kind.value} {self._execution.id} "
            f"backend={self.backend!r} state={self._execution.state.value!r}>"
        )

    # -- live operations ------------------------------------------------------- #
    async def refresh(self) -> Execution:
        """Re-read from the backend. A terminal snapshot is never re-read."""
        async with self._lock:
            if self._execution.is_terminal:
                return self._execution
        fresh = await self._backend.aget(self.id)
        async with self._lock:
            self._execution = fresh
        return fresh

    async def status(self) -> ExecutionState:
        return (await self.refresh()).state

    async def wait(self, timeout: float | None = None) -> Execution:
        """Await terminal state without blocking the event loop."""
        async with self._lock:
            if self._execution.is_terminal:
                return self._execution
        final = await self._backend.await_(self.id, timeout=timeout)
        async with self._lock:
            self._execution = final
        return final

    async def result(self, timeout: float | None = None) -> ExecutionResult:
        """Return the outcome, raising :class:`~taskferry.errors.ExecutionError` on failure."""
        outcome = await self._backend.aresult(self.id, timeout=timeout)
        if outcome.error is not None:
            raise ExecutionError(
                f"{self.kind.value} {self.id} failed: {outcome.error}",
                backend=self.backend,
                cause_repr=outcome.traceback or outcome.error,
            )
        return outcome

    async def value(self, timeout: float | None = None) -> Any:
        """The value the execution produced."""
        return (await self.result(timeout)).value

    async def cancel(self) -> Execution:
        if not self._backend.capabilities.supports(Capability.CANCEL):
            raise UnsupportedCapability(Capability.CANCEL.value, provider=self.backend)
        cancelled = await self._backend.acancel(self.id)
        async with self._lock:
            self._execution = cancelled
        return cancelled


class AsyncTaskferry:
    """The portable execution layer, for async applications.

    Args:
        runtime: An existing :class:`~taskferry.runtime.Taskferry` to share. When
            omitted, one is built from ``**kwargs`` exactly as the sync
            constructor would.

    Sharing a runtime is the recommended pattern in a process that has both an
    async web surface and sync management commands — one set of backends, one
    connection pool, one execution index.
    """

    def __init__(self, runtime: Taskferry | None = None, /, **kwargs: Any) -> None:
        self._runtime = runtime if runtime is not None else Taskferry(**kwargs)
        self.inline = AsyncInlineFacade(self)
        self.tasks = AsyncTaskFacade(self)
        self.jobs = AsyncJobFacade(self)

    # -- constructors ---------------------------------------------------------- #
    @classmethod
    def local(cls, **kwargs: Any) -> AsyncTaskferry:
        """A runtime that needs no infrastructure. See :meth:`Taskferry.local`."""
        return cls(Taskferry.local(**kwargs))

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, **kwargs: Any) -> AsyncTaskferry:
        return cls(Taskferry.from_env(environ, **kwargs))

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], **kwargs: Any) -> AsyncTaskferry:
        return cls(Taskferry.from_mapping(data, **kwargs))

    # -- introspection (shared, synchronous — no I/O) --------------------------- #
    @property
    def sync(self) -> Taskferry:
        """The underlying sync runtime. Use it from sync code in the same process."""
        return self._runtime

    @property
    def config(self) -> TaskferryConfig:
        return self._runtime.config

    @property
    def router(self) -> Router:
        return self._runtime.router

    @property
    def registry(self) -> Any:
        return self._runtime.registry

    def backend_names(self) -> tuple[str, ...]:
        return self._runtime.backend_names()

    def backend(self, name: str) -> ExecutionBackend:
        return self._runtime.backend(name)

    def capabilities(self, name: str) -> CapabilitySet:
        return self._runtime.capabilities(name)

    def describe(self) -> dict[str, Any]:
        return self._runtime.describe()

    def __repr__(self) -> str:
        return f"<AsyncTaskferry backends={list(self.backend_names())}>"

    # -- submission -------------------------------------------------------------- #
    async def submit(
        self,
        spec: AnySpec,
        *,
        backend: str | None = None,
        idempotency_key: str | None = None,
    ) -> AsyncExecutionHandle:
        """Route ``spec`` to a backend and submit it without blocking the loop."""
        target, resolved, prepared = self._runtime._prepare(
            spec, backend=backend, idempotency_key=idempotency_key
        )
        execution = await target.asubmit(prepared)
        self._runtime._track(execution.id, resolved)
        return AsyncExecutionHandle(execution, target)

    async def get(
        self, execution_id: ExecutionId | str, *, backend: str | None = None
    ) -> AsyncExecutionHandle:
        target = self._runtime._owner_backend(execution_id, backend)
        execution = await target.aget(ExecutionId(str(execution_id)))
        return AsyncExecutionHandle(execution, target)

    async def cancel(
        self, execution_id: ExecutionId | str, *, backend: str | None = None
    ) -> Execution:
        return await (await self.get(execution_id, backend=backend)).cancel()

    async def wait(
        self,
        execution_id: ExecutionId | str,
        *,
        timeout: float | None = None,
        backend: str | None = None,
    ) -> Execution:
        return await (await self.get(execution_id, backend=backend)).wait(timeout)

    async def result(
        self,
        execution_id: ExecutionId | str,
        *,
        timeout: float | None = None,
        backend: str | None = None,
    ) -> ExecutionResult:
        return await (await self.get(execution_id, backend=backend)).result(timeout)

    # -- lifecycle ---------------------------------------------------------------- #
    async def close(self) -> None:
        """Release every backend's resources, off the event loop."""
        await asyncio.to_thread(self._runtime.close)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()


class _AsyncFacade:
    __slots__ = ("_aio",)

    def __init__(self, aio: AsyncTaskferry) -> None:
        self._aio = aio

    async def submit_spec(
        self, spec: AnySpec, *, backend: str | None = None
    ) -> AsyncExecutionHandle:
        """Submit an already-built spec."""
        return await self._aio.submit(spec, backend=backend)


class AsyncInlineFacade(_AsyncFacade):
    """``runtime.inline`` — run a callable now, without blocking the loop.

    "Inline" still means "this process", but on the async surface the work runs
    on a worker thread so the loop keeps serving. An ``async def`` callable is
    awaited on that thread's own loop.

    If you are already in async code and the work is a coroutine, `await` it
    directly — you do not need Taskferry to run something in the process you are
    already in. Use this when the callable is *sync* and would otherwise block.
    """

    __slots__ = ()

    async def submit(
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
    ) -> AsyncExecutionHandle:
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
        return await self._aio.submit(spec, backend=backend)

    async def run(self, func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """Run ``func`` and return its value, raising on failure."""
        handle = await self.submit(func, *args, **kwargs)
        return await handle.value()


class AsyncTaskFacade(_AsyncFacade):
    """``runtime.tasks`` — hand a named function to a task engine."""

    __slots__ = ()

    async def submit(
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
    ) -> AsyncExecutionHandle:
        ref = self._aio.registry.reference(task)
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
        return await self._aio.submit(spec, backend=backend)


class AsyncJobFacade(_AsyncFacade):
    """``runtime.jobs`` — run an isolated workload to completion."""

    __slots__ = ()

    async def submit(
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
    ) -> AsyncExecutionHandle:
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
        return await self._aio.submit(spec, backend=backend)


__all__ = [
    "AsyncExecutionHandle",
    "AsyncInlineFacade",
    "AsyncJobFacade",
    "AsyncTaskFacade",
    "AsyncTaskferry",
]
