"""The async surface: same vocabulary, real non-blocking behaviour.

The tests that matter most here are the two that would pass trivially if the
async path were an ``async def`` painted over a blocking call — the event-loop
responsiveness check and the concurrency check.
"""

from __future__ import annotations

import asyncio
import sys
import time

import pytest

import tasks_fixture
from taskport import (
    AsyncExecutionHandle,
    AsyncTaskport,
    Capability,
    ExecutionKind,
    ExecutionState,
    Resources,
    Taskport,
    TaskSpec,
)
from taskport.errors import ExecutionError, ExecutionNotFound, RoutingError, UnsupportedCapability


@pytest.fixture
async def aio() -> AsyncTaskport:
    runtime = AsyncTaskport.local()
    yield runtime
    await runtime.close()


class TestTheSameVocabulary:
    """Porting a module between surfaces should be adding `await`, nothing else."""

    def test_method_names_match_the_sync_runtime(self) -> None:
        sync = {"submit", "get", "cancel", "wait", "result", "close"}
        assert sync <= set(dir(AsyncTaskport))

    def test_handle_method_names_match(self) -> None:
        from taskport import ExecutionHandle

        shared = {"refresh", "status", "wait", "result", "value", "cancel"}
        assert shared <= set(dir(ExecutionHandle))
        assert shared <= set(dir(AsyncExecutionHandle))

    def test_facade_names_match(self) -> None:
        runtime = AsyncTaskport.local()
        for facade in ("inline", "tasks", "jobs"):
            assert hasattr(runtime, facade)
            assert hasattr(getattr(runtime, facade), "submit")


class TestThreePrimitives:
    async def test_inline(self, aio: AsyncTaskport) -> None:
        handle = await aio.inline.submit(lambda a, b: a + b, 20, 22)
        assert await handle.value() == 42
        assert handle.kind is ExecutionKind.INLINE

    async def test_inline_run_returns_the_value(self, aio: AsyncTaskport) -> None:
        assert await aio.inline.run(tasks_fixture.add, 20, 22) == 42

    async def test_task(self, aio: AsyncTaskport) -> None:
        handle = await aio.tasks.submit(tasks_fixture.add, 1, 2)
        assert (await handle.wait(10)).state is ExecutionState.SUCCEEDED
        assert await handle.value() == 3

    async def test_job(self, aio: AsyncTaskport) -> None:
        handle = await aio.jobs.submit("echo", command=[sys.executable, "-c", "print('hi')"])
        assert (await handle.wait(30)).state is ExecutionState.SUCCEEDED
        assert (await handle.result()).exit_code == 0

    async def test_an_async_task_function_is_awaited(self, aio: AsyncTaskport) -> None:
        handle = await aio.tasks.submit(tasks_fixture.async_double, 21)
        assert await handle.value(10) == 42


class TestItReallyDoesNotBlockTheLoop:
    """The whole reason the async surface exists."""

    async def test_the_loop_keeps_running_during_a_slow_job(self, aio: AsyncTaskport) -> None:
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        try:
            handle = await aio.jobs.submit(
                "slow", command=[sys.executable, "-c", "import time; time.sleep(0.4)"]
            )
            await handle.wait(30)
        finally:
            beat.cancel()

        assert ticks > 5, (
            f"the event loop only ticked {ticks} times while waiting — the async path "
            f"is blocking the loop instead of running off it"
        )

    async def test_submissions_run_concurrently(self, aio: AsyncTaskport) -> None:
        """Four 200ms jobs should take ~200ms, not ~800ms."""
        started = time.monotonic()
        handles = await asyncio.gather(
            *(
                aio.jobs.submit(
                    f"j{n}", command=[sys.executable, "-c", "import time; time.sleep(0.2)"]
                )
                for n in range(4)
            )
        )
        await asyncio.gather(*(handle.wait(30) for handle in handles))
        elapsed = time.monotonic() - started

        assert all(h.done for h in handles)
        assert elapsed < 0.7, f"four concurrent 0.2s jobs took {elapsed:.2f}s — they serialised"

    async def test_inline_of_a_blocking_call_does_not_stall_the_loop(
        self, aio: AsyncTaskport
    ) -> None:
        """The intended use of the async inline facade: a sync call that blocks."""
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        try:
            assert await aio.inline.run(tasks_fixture.slow, 0.3) == "done"
        finally:
            beat.cancel()

        assert ticks > 5, f"the loop only ticked {ticks} times during a 0.3s sync call"


class TestSharedRuntime:
    """One process, two surfaces, one set of backends."""

    def test_wrapping_shares_everything(self) -> None:
        sync = Taskport.local()
        aio = AsyncTaskport(sync)
        assert aio.sync is sync
        assert aio.config is sync.config
        assert aio.router is sync.router
        assert aio.registry is sync.registry
        assert aio.backend("inline") is sync.backend("inline")
        sync.close()

    async def test_an_execution_submitted_sync_is_visible_async(self) -> None:
        """One execution index, so neither surface loses track of the other's work."""
        sync = Taskport.local()
        aio = AsyncTaskport(sync)

        handle = sync.tasks.submit(tasks_fixture.add, 1, 1)
        handle.wait(10)

        found = await aio.get(handle.id)
        assert found.state is ExecutionState.SUCCEEDED
        assert await found.value() == 2
        await aio.close()

    async def test_an_execution_submitted_async_is_visible_sync(self) -> None:
        sync = Taskport.local()
        aio = AsyncTaskport(sync)

        handle = await aio.tasks.submit(tasks_fixture.add, 2, 2)
        await handle.wait(10)

        assert sync.get(handle.id).result().value == 4
        await aio.close()


class TestRoutingAndCapabilitiesAreShared:
    """The async surface must not be a second, subtly different code path."""

    async def test_routing_is_the_same(self) -> None:
        config = {
            "backends": {
                "fast": {"factory": "thread"},
                "slow": {"factory": "process", "max_workers": 1},
            },
            "routes": [{"kind": "task", "queue": "heavy", "backend": "slow"}],
            "defaults": {"task": "fast", "inline": "fast"},
        }
        sync = Taskport.from_mapping(config)
        aio = AsyncTaskport(sync)

        spec = TaskSpec(task="tasks_fixture:add", queue="heavy")
        assert sync.router.resolve(spec) == "slow"
        assert aio.router.resolve(spec) == "slow"

        handle = await aio.tasks.submit(tasks_fixture.pid, queue="heavy")
        import os

        assert await handle.value(30) != os.getpid()
        await aio.close()

    async def test_capability_refusals_are_identical(self, aio: AsyncTaskport) -> None:
        with pytest.raises(UnsupportedCapability, match="priority"):
            await aio.tasks.submit(tasks_fixture.add, 1, 1, priority=9)
        with pytest.raises(UnsupportedCapability, match="gpu"):
            await aio.jobs.submit("t", command=["true"], resources=Resources(gpu=1))

    async def test_a_wrong_kind_backend_is_refused(self, aio: AsyncTaskport) -> None:
        with pytest.raises(RoutingError, match="job backend"):
            await aio.submit(TaskSpec(task="tasks_fixture:add", args=(1, 1)), backend="subprocess")

    async def test_an_unknown_id_explains_itself(self, aio: AsyncTaskport) -> None:
        with pytest.raises(ExecutionNotFound, match="pass backend="):
            await aio.get("task_never_submitted")

    async def test_cancel_is_refused_when_unsupported(self, aio: AsyncTaskport) -> None:
        handle = await aio.inline.submit(tasks_fixture.add, 1, 1)
        with pytest.raises(UnsupportedCapability, match="cancel"):
            await handle.cancel()

    async def test_cancel_works_where_supported(self, aio: AsyncTaskport) -> None:
        handle = await aio.jobs.submit(
            "long", command=[sys.executable, "-c", "import time; time.sleep(30)"]
        )
        assert Capability.CANCEL in handle.capabilities
        assert (await handle.cancel()).state is ExecutionState.CANCELLED


class TestFailures:
    async def test_a_failure_raises_from_result_not_from_submit(self, aio: AsyncTaskport) -> None:
        handle = await aio.tasks.submit(tasks_fixture.boom)
        assert (await handle.wait(10)).state is ExecutionState.FAILED
        with pytest.raises(ExecutionError, match="task failed on purpose"):
            await handle.result()

    async def test_a_timeout_raises_rather_than_hanging(self, aio: AsyncTaskport) -> None:
        from taskport.errors import TaskportTimeoutError

        handle = await aio.jobs.submit(
            "slow", command=[sys.executable, "-c", "import time; time.sleep(10)"]
        )
        with pytest.raises(TaskportTimeoutError):
            await handle.wait(0.2)
        await handle.cancel()

    async def test_a_terminal_handle_is_not_re_polled(self, aio: AsyncTaskport) -> None:
        handle = await aio.inline.submit(tasks_fixture.add, 1, 1)
        first = await handle.refresh()
        assert await handle.refresh() is first


class TestLifecycle:
    async def test_async_context_manager(self) -> None:
        async with AsyncTaskport.local() as runtime:
            assert await runtime.inline.run(tasks_fixture.add, 1, 1) == 2

    async def test_close_does_not_block_the_loop(self) -> None:
        runtime = AsyncTaskport.local()
        await runtime.tasks.submit(tasks_fixture.add, 1, 1)
        await runtime.close()

    def test_constructors_mirror_the_sync_ones(self) -> None:
        for build in (
            lambda: AsyncTaskport.local(),
            lambda: AsyncTaskport.from_env({}),
            lambda: AsyncTaskport.from_mapping(
                {"backends": {"i": {"factory": "inline"}}, "defaults": {"inline": "i"}}
            ),
        ):
            runtime = build()
            assert isinstance(runtime, AsyncTaskport)
            runtime.sync.close()

    def test_describe_works_without_an_event_loop(self) -> None:
        """Introspection is sync on both surfaces — it does no I/O."""
        runtime = AsyncTaskport.local()
        assert set(runtime.describe()) == {"backends", "routes", "defaults"}
        assert runtime.backend_names() == ("inline", "subprocess", "thread")
        runtime.sync.close()


class TestBackendAsyncPort:
    """The adapter-facing surface, which uses the `a` prefix."""

    async def test_every_builtin_backend_has_the_async_surface(self) -> None:
        runtime = Taskport.local()
        for name in runtime.backend_names():
            backend = runtime.backend(name)
            for method in ("asubmit", "aget", "acancel", "aresult", "await_"):
                assert callable(getattr(backend, method)), f"{name} is missing {method}"
        runtime.close()

    async def test_the_default_async_path_delegates_correctly(self) -> None:
        from taskport.backends.thread import ThreadTaskBackend
        from taskport.functions import FunctionRegistry

        registry = FunctionRegistry()
        registry.register(tasks_fixture.add, name="tasks_fixture:add")
        backend = ThreadTaskBackend(registry=registry)

        execution = await backend.asubmit(TaskSpec(task="tasks_fixture:add", args=(1, 2)))
        final = await backend.await_(execution.id, timeout=10)
        assert final.state is ExecutionState.SUCCEEDED
        assert (await backend.aresult(execution.id)).value == 3
        assert (await backend.aget(execution.id)).id == execution.id
        backend.close()

    async def test_an_adapter_can_override_the_async_path(self) -> None:
        """A natively-async client skips the thread; nothing else has to change."""
        from taskport.backends.inline import InlineExecutionBackend
        from taskport.specs import ExecutionSpec

        calls: list[str] = []

        class NativeAsync(InlineExecutionBackend):
            async def asubmit(self, spec: ExecutionSpec) -> object:
                calls.append("native")
                return self.submit(spec)

        backend = NativeAsync()
        await backend.asubmit(
            tasks_fixture
            and __import__("taskport").specs.InlineSpec(func=tasks_fixture.add, args=(1, 1))
        )
        assert calls == ["native"]
