"""The runtime, the router, and the handle — the parts an application touches."""

from __future__ import annotations

import threading

import pytest

import tasks_fixture
from taskport import (
    BaseHook,
    Capability,
    ExecutionKind,
    ExecutionState,
    JobSpec,
    RetryPolicy,
    Route,
    Router,
    Taskport,
    TaskportConfig,
    TaskSpec,
    TimeoutPolicy,
)
from taskport.config import BackendConfig
from taskport.errors import (
    ConfigurationError,
    ExecutionError,
    ExecutionNotFound,
    RoutingError,
    UnsupportedCapability,
)
from taskport.specs import InlineSpec


@pytest.fixture(autouse=True)
def _clean_fixture_state() -> None:
    tasks_fixture.reset_attempts()


@pytest.fixture
def runtime() -> Taskport:
    rt = Taskport.local()
    yield rt
    rt.close()


class TestAcceptanceCriteria:
    """The exact scenario the brief requires to work, verbatim."""

    def test_pure_python_with_no_infrastructure(self) -> None:
        from taskport import Taskport as ImportedTaskport

        rt = ImportedTaskport.local()

        def add(a: int, b: int) -> int:
            return a + b

        execution = rt.inline.submit(add, 20, 42 - 20)
        assert execution.result().value == 42
        rt.close()

    def test_all_three_primitives_from_one_runtime(self, runtime: Taskport) -> None:
        """Immediate, background and isolated execution, no infrastructure."""
        inline = runtime.inline.submit(tasks_fixture.add, 1, 1)
        task = runtime.tasks.submit(tasks_fixture.add, 2, 2)
        job = runtime.jobs.submit("echo", command=["python", "-c", "print('hi')"])

        assert inline.result().value == 2
        assert task.wait(10).state is ExecutionState.SUCCEEDED
        assert task.result().value == 4
        assert job.wait(30).state is ExecutionState.SUCCEEDED
        assert job.result().exit_code == 0

    def test_the_three_kinds_stay_distinct(self, runtime: Taskport) -> None:
        """A Job is not a slow Task; the runtime never blurs them."""
        assert runtime.inline.submit(tasks_fixture.add, 1, 1).kind is ExecutionKind.INLINE
        assert runtime.tasks.submit(tasks_fixture.add, 1, 1).kind is ExecutionKind.TASK
        assert runtime.jobs.submit("j", command=["true"]).kind is ExecutionKind.JOB


class TestInlineFacade:
    def test_run_returns_the_value_directly(self, runtime: Taskport) -> None:
        assert runtime.inline.run(tasks_fixture.add, 20, 22) == 42

    def test_a_closure_works(self, runtime: Taskport) -> None:
        """Inline is the primitive where no importable name is needed."""
        offset = 10
        assert runtime.inline.run(lambda x: x + offset, 5) == 15

    def test_a_failure_surfaces_from_result_not_from_submit(self, runtime: Taskport) -> None:
        handle = runtime.inline.submit(tasks_fixture.boom)
        assert handle.state is ExecutionState.FAILED
        with pytest.raises(ExecutionError, match="task failed on purpose"):
            handle.result()

    async def test_an_async_callable_is_awaited(self, runtime: Taskport) -> None:
        handle = runtime.inline.submit(tasks_fixture.async_double, 21)
        assert handle.result().value == 42

    def test_retries_are_applied_by_the_inline_backend(self, runtime: Taskport) -> None:
        """Inline *is* the engine, so it owns the retry and really performs it."""
        handle = runtime.inline.submit(
            tasks_fixture.flaky,
            "inline-retry",
            2,
            retry=RetryPolicy(max_attempts=3, initial_delay=0.0),
        )
        assert handle.result().value == "succeeded on attempt 3"
        assert handle.execution.attempt == 3

    def test_a_timeout_is_refused_rather_than_ignored(self, runtime: Taskport) -> None:
        """Nothing can preempt a synchronous call, so inline must not claim to."""
        spec = InlineSpec(func=tasks_fixture.slow, timeout=TimeoutPolicy(seconds=1))
        with pytest.raises(UnsupportedCapability):
            runtime.submit(spec)


class TestTaskFacade:
    def test_a_callable_is_accepted_and_registered(self, runtime: Taskport) -> None:
        """Submit the function; the portable name is derived and remembered."""
        handle = runtime.tasks.submit(tasks_fixture.add, 3, 4)
        assert handle.wait(10).state is ExecutionState.SUCCEEDED
        assert handle.result().value == 7
        assert "tasks_fixture:add" in runtime.registry

    def test_a_name_string_is_accepted(self, runtime: Taskport) -> None:
        handle = runtime.tasks.submit("tasks_fixture:add", 5, 6)
        assert handle.result(10).value == 11

    def test_a_lambda_is_rejected_with_an_explanation(self, runtime: Taskport) -> None:
        """A worker could never find it, so failing here is the kind thing."""
        from taskport.errors import FunctionResolutionError

        with pytest.raises(FunctionResolutionError, match="cannot be resolved by a remote"):
            runtime.tasks.submit(lambda: None)

    def test_a_failure_is_recorded_on_the_execution(self, runtime: Taskport) -> None:
        handle = runtime.tasks.submit(tasks_fixture.boom)
        assert handle.wait(10).state is ExecutionState.FAILED
        assert handle.execution.result is not None
        assert handle.execution.result.error_type == "ValueError"

    def test_delay_is_honoured(self, runtime: Taskport) -> None:
        handle = runtime.tasks.submit(tasks_fixture.add, 1, 1, delay=0.15)
        assert handle.refresh().state in {ExecutionState.QUEUED, ExecutionState.RUNNING}
        assert handle.wait(10).state is ExecutionState.SUCCEEDED

    def test_priority_is_refused_by_a_fifo_backend(self, runtime: Taskport) -> None:
        """A thread pool is FIFO. Accepting a priority would be a quiet lie."""
        with pytest.raises(UnsupportedCapability, match="priority"):
            runtime.tasks.submit(tasks_fixture.add, 1, 1, priority=9)

    def test_an_idempotency_key_is_refused_when_unsupported(self, runtime: Taskport) -> None:
        with pytest.raises(UnsupportedCapability, match="deduplication"):
            runtime.tasks.submit(tasks_fixture.add, 1, 1, idempotency_key="k")

    async def test_an_async_task_function_runs(self, runtime: Taskport) -> None:
        handle = runtime.tasks.submit(tasks_fixture.async_double, 21)
        assert handle.result(10).value == 42


class TestJobFacade:
    def test_a_failing_job_reports_its_exit_code(self, runtime: Taskport) -> None:
        handle = runtime.jobs.submit("fail", command=["python", "-c", "raise SystemExit(3)"])
        assert handle.wait(30).state is ExecutionState.FAILED
        assert handle.execution.result is not None
        assert handle.execution.result.exit_code == 3

    def test_environment_variables_reach_the_child(self, runtime: Taskport) -> None:
        handle = runtime.jobs.submit(
            "env",
            command=["python", "-c", "import os,sys; sys.exit(0 if os.environ['TP']=='42' else 1)"],
            env={"TP": "42"},
        )
        assert handle.wait(30).state is ExecutionState.SUCCEEDED

    def test_a_gpu_request_is_refused_by_a_subprocess_backend(self, runtime: Taskport) -> None:
        """The assertion that matters most: never silently run GPU work on a CPU."""
        from taskport import Resources

        with pytest.raises(UnsupportedCapability, match="gpu"):
            runtime.jobs.submit("train", command=["true"], resources=Resources(gpu=1))

    def test_a_timeout_really_kills_the_child(self, runtime: Taskport) -> None:
        handle = runtime.jobs.submit(
            "sleepy", command=["python", "-c", "import time; time.sleep(30)"], timeout=0.3
        )
        final = handle.wait(30)
        assert final.state is ExecutionState.TIMED_OUT

    def test_cancel_stops_a_running_job(self, runtime: Taskport) -> None:
        handle = runtime.jobs.submit(
            "long", command=["python", "-c", "import time; time.sleep(30)"]
        )
        cancelled = handle.cancel()
        assert cancelled.state is ExecutionState.CANCELLED


class TestRouter:
    """Routing is pure: same spec plus same rules, same answer, no backends needed."""

    def test_first_match_wins(self) -> None:
        router = Router(
            [
                Route(backend="fast", kind=ExecutionKind.TASK, queue="metadata"),
                Route(backend="slow", kind=ExecutionKind.TASK),
            ]
        )
        assert router.resolve(TaskSpec(task="m:f", queue="metadata")) == "fast"
        assert router.resolve(TaskSpec(task="m:f", queue="other")) == "slow"

    def test_queue_patterns_are_globs(self) -> None:
        router = Router([Route(backend="media", queue="media-*")])
        assert router.resolve(TaskSpec(task="m:f", queue="media-thumbs")) == "media"

    def test_profile_routes_jobs(self) -> None:
        """Jobs route on profile the way tasks route on queue."""
        router = Router(
            [
                Route(backend="gpu-pool", kind=ExecutionKind.JOB, profile="gpu"),
                Route(backend="batch", kind=ExecutionKind.JOB),
            ]
        )
        assert router.resolve(JobSpec(job="train", profile="gpu")) == "gpu-pool"
        assert router.resolve(JobSpec(job="etl")) == "batch"

    def test_labels_must_all_match(self) -> None:
        router = Router([Route(backend="eu", labels={"region": "eu", "tier": "gold"})])
        assert router.resolve(TaskSpec(task="m:f", labels={"region": "eu", "tier": "gold"})) == "eu"
        with pytest.raises(RoutingError):
            router.resolve(TaskSpec(task="m:f", labels={"region": "eu"}))

    def test_the_default_applies_when_nothing_matches(self) -> None:
        router = Router(defaults={ExecutionKind.TASK: "fallback"})
        assert router.resolve(TaskSpec(task="m:f")) == "fallback"

    def test_an_unroutable_spec_raises_and_says_what_was_tried(self) -> None:
        """Falling back to "whatever is around" is how work reaches the wrong engine."""
        router = Router([Route(backend="a", queue="specific")])
        with pytest.raises(RoutingError) as caught:
            router.resolve(TaskSpec(task="m:f", queue="unmatched"))
        assert "routes tried" in str(caught.value)
        assert "queue=specific" in str(caught.value)

    def test_explain_reports_which_rule_matched(self) -> None:
        router = Router([Route(backend="a", queue="q")], defaults={ExecutionKind.TASK: "d"})
        assert "route #0" in router.explain(TaskSpec(task="m:f", queue="q"))
        assert "default" in router.explain(TaskSpec(task="m:f", queue="other"))

    def test_is_immutable(self) -> None:
        original = Router([Route(backend="a")])
        extended = original.with_route(Route(backend="b"))
        assert len(original.routes) == 1
        assert len(extended.routes) == 2


class TestConfiguration:
    def test_a_route_naming_an_unknown_backend_fails_at_construction(self) -> None:
        """A typo in a route surfaces at startup, not on first use in production."""
        with pytest.raises(ConfigurationError, match="unknown backend"):
            TaskportConfig(
                backends={"real": BackendConfig(factory="inline")},
                routes=(Route(backend="typo"),),
            )

    def test_a_default_naming_an_unknown_backend_fails(self) -> None:
        with pytest.raises(ConfigurationError, match="not defined"):
            TaskportConfig(
                backends={"real": BackendConfig(factory="inline")},
                defaults={ExecutionKind.TASK: "missing"},
            )

    def test_from_mapping_builds_the_whole_configuration(self) -> None:
        config = TaskportConfig.from_mapping(
            {
                "backends": {"t": {"factory": "thread", "max_workers": 2}},
                "routes": [{"kind": "task", "queue": "metadata", "backend": "t"}],
                "defaults": {"task": "t"},
            }
        )
        assert config.backends["t"].options["max_workers"] == 2
        assert config.router().resolve(TaskSpec(task="m:f", queue="metadata")) == "t"

    def test_from_env_reads_json_structure(self) -> None:
        config = TaskportConfig.from_env(
            {
                "TASKPORT_BACKENDS": '{"t": {"factory": "thread"}}',
                "TASKPORT_ROUTES": '[{"kind": "task", "queue": "q", "backend": "t"}]',
                "TASKPORT_DEFAULT_TASK": "t",
            }
        )
        assert config.router().resolve(TaskSpec(task="m:f", queue="q")) == "t"

    def test_from_env_falls_back_to_local_when_nothing_is_set(self) -> None:
        """A container that forgot to configure Taskport still starts and still runs."""
        config = TaskportConfig.from_env({})
        assert set(config.backends) == {"inline", "thread", "subprocess"}

    def test_malformed_env_json_says_which_variable(self) -> None:
        with pytest.raises(ConfigurationError, match="TASKPORT_BACKENDS"):
            TaskportConfig.from_env({"TASKPORT_BACKENDS": "{not json"})

    def test_configuration_is_immutable(self) -> None:
        config = TaskportConfig.local()
        with pytest.raises(AttributeError):
            config.allow_import = False  # type: ignore[misc]


class TestRuntimeWiring:
    def test_backends_are_built_lazily_and_cached(self) -> None:
        """Configuring a cloud backend must cost a web process nothing."""
        built: list[str] = []

        def factory(**options: object) -> object:
            from taskport.backends.inline import InlineExecutionBackend

            built.append("yes")
            return InlineExecutionBackend()

        from taskport import register_backend, unregister_backend

        register_backend("counting", factory, replace=True)
        try:
            rt = Taskport(
                config=TaskportConfig(
                    backends={"c": BackendConfig(factory="counting")},
                    defaults={ExecutionKind.INLINE: "c"},
                )
            )
            assert built == [], "constructing the runtime must not build backends"
            rt.backend("c")
            rt.backend("c")
            assert built == ["yes"], "a backend is built once and cached"
            rt.close()
        finally:
            unregister_backend("counting")

    def test_an_unknown_backend_name_lists_what_is_configured(self) -> None:
        rt = Taskport.local()
        with pytest.raises(ConfigurationError, match="configured: inline, subprocess, thread"):
            rt.backend("nope")
        rt.close()

    def test_a_backend_of_the_wrong_kind_is_refused(self, runtime: Taskport) -> None:
        """Pinning a task at a job backend is a configuration error, caught early."""
        with pytest.raises(RoutingError, match="job backend"):
            runtime.submit(TaskSpec(task="tasks_fixture:add", args=(1, 1)), backend="subprocess")

    def test_injected_backends_take_precedence(self) -> None:
        from taskport.backends.inline import InlineExecutionBackend

        injected = InlineExecutionBackend()
        rt = Taskport(config=TaskportConfig.local(), backends={"inline": injected})
        assert rt.backend("inline") is injected
        rt.close()

    def test_get_finds_the_owning_backend_without_being_told(self, runtime: Taskport) -> None:
        handle = runtime.tasks.submit(tasks_fixture.add, 1, 1)
        handle.wait(10)
        assert runtime.get(handle.id).state is ExecutionState.SUCCEEDED

    def test_get_on_an_unknown_id_explains_how_to_recover(self, runtime: Taskport) -> None:
        """Across a process boundary the index is gone; say so rather than guess."""
        with pytest.raises(ExecutionNotFound, match="pass backend="):
            runtime.get("task_never_submitted")

    def test_the_execution_index_is_bounded(self) -> None:
        """A long-lived producer must not grow a map forever."""
        rt = Taskport(config=TaskportConfig.local().evolve(max_tracked_executions=3))
        handles = [rt.inline.submit(tasks_fixture.add, 1, 1) for _ in range(5)]
        with pytest.raises(ExecutionNotFound):
            rt.get(handles[0].id)
        assert rt.get(handles[-1].id) is not None
        rt.close()

    def test_correlation_is_attached_automatically(self, runtime: Taskport) -> None:
        handle = runtime.inline.submit(tasks_fixture.add, 1, 1)
        assert handle.execution.correlation is not None

    def test_a_runtime_is_a_context_manager(self) -> None:
        with Taskport.local() as rt:
            assert rt.inline.run(tasks_fixture.add, 1, 1) == 2

    def test_describe_does_not_build_backends(self) -> None:
        """Inspecting a configuration must work where an SDK is missing."""
        rt = Taskport(
            config=TaskportConfig(
                backends={"cloud": BackendConfig(factory="cloudrun", options={"project": "p"})},
                defaults={ExecutionKind.JOB: "cloud"},
            )
        )
        described = rt.describe()
        assert described["backends"]["cloud"]["built"] is False
        assert described["defaults"]["job"] == "cloud"
        rt.close()


class TestExecutionHandle:
    def test_a_terminal_snapshot_is_not_re_polled(self, runtime: Taskport) -> None:
        """Polling a finished execution wastes an engine round-trip."""
        handle = runtime.inline.submit(tasks_fixture.add, 1, 1)
        first = handle.refresh()
        assert handle.refresh() is first

    def test_cancel_is_refused_when_unsupported(self, runtime: Taskport) -> None:
        handle = runtime.inline.submit(tasks_fixture.add, 1, 1)
        with pytest.raises(UnsupportedCapability, match="cancel"):
            handle.cancel()

    def test_capabilities_are_reachable_from_the_handle(self, runtime: Taskport) -> None:
        """Ask before you act — that is the whole capability contract."""
        handle = runtime.tasks.submit(tasks_fixture.add, 1, 1)
        assert Capability.CANCEL in handle.capabilities

    def test_wait_times_out_rather_than_hanging(self, runtime: Taskport) -> None:
        from taskport.errors import TaskportTimeoutError

        handle = runtime.jobs.submit(
            "slow", command=["python", "-c", "import time; time.sleep(10)"]
        )
        with pytest.raises(TaskportTimeoutError):
            handle.wait(0.2)
        handle.cancel()


class TestHooks:
    def test_hooks_observe_the_inline_lifecycle(self) -> None:
        """Inline fires the execution hooks *inside* submission.

        There is no queue: by the time ``submit`` returns, the work is over. So
        ``on_success`` genuinely precedes ``after_submit`` here, and that is the
        honest ordering rather than an accident. A hook that needs "submitted"
        and "finished" to be distinct moments should read ``execution.state``
        rather than assume a queue exists.
        """
        events: list[str] = []

        class Recorder(BaseHook):
            def before_submit(self, spec: object, backend: str) -> None:
                events.append(f"before_submit:{backend}")

            def before_execute(self, execution: object) -> None:
                events.append("before_execute")

            def on_success(self, execution: object, result: object) -> None:
                events.append("on_success")

            def after_submit(self, spec: object, execution: object) -> None:
                events.append("after_submit")

        rt = Taskport.local(hooks=[Recorder()])
        rt.inline.submit(tasks_fixture.add, 1, 1)
        assert events == [
            "before_submit:inline",
            "before_execute",
            "on_success",
            "after_submit",
        ]
        rt.close()

    def test_submit_side_and_execute_side_hooks_are_not_ordered_against_each_other(
        self,
    ) -> None:
        """Once execution is concurrent, only each side's own ordering holds.

        A worker can begin before ``after_submit`` returns in the submitting
        thread — on a thread pool because it is fast, on a real queue because the
        worker is in a different process entirely and no ordering is possible.
        The guarantee Taskport makes is per-side: ``before_submit`` precedes
        ``after_submit``, ``before_execute`` precedes ``on_success``. A hook that
        assumes more than that is relying on a race it will eventually lose.
        """
        submit_side: list[str] = []
        execute_side: list[str] = []
        finished = threading.Event()

        class Recorder(BaseHook):
            def before_submit(self, spec: object, backend: str) -> None:
                submit_side.append("before_submit")

            def after_submit(self, spec: object, execution: object) -> None:
                submit_side.append("after_submit")

            def before_execute(self, execution: object) -> None:
                execute_side.append("before_execute")

            def on_success(self, execution: object, result: object) -> None:
                execute_side.append("on_success")
                finished.set()

        rt = Taskport.local(hooks=[Recorder()])
        rt.tasks.submit(tasks_fixture.add, 1, 1)
        assert finished.wait(10)
        assert submit_side == ["before_submit", "after_submit"]
        assert execute_side == ["before_execute", "on_success"]
        rt.close()

    def test_a_failing_hook_never_breaks_the_work(self) -> None:
        """A telemetry outage must not take the application down with it."""

        class Broken(BaseHook):
            def after_submit(self, spec: object, execution: object) -> None:
                raise RuntimeError("metrics backend is down")

        rt = Taskport.local(hooks=[Broken()])
        assert rt.inline.run(tasks_fixture.add, 20, 22) == 42
        rt.close()

    def test_a_retry_is_reported(self) -> None:
        attempts: list[int] = []

        class Watcher(BaseHook):
            def on_retry(self, execution: object, attempt: int, exc: BaseException) -> None:
                attempts.append(attempt)

        rt = Taskport.local(hooks=[Watcher()])
        rt.inline.submit(
            tasks_fixture.flaky,
            "hook-retry",
            2,
            retry=RetryPolicy(max_attempts=3, initial_delay=0.0),
        )
        assert attempts == [2, 3]
        rt.close()


class TestThreadSafety:
    def test_concurrent_submits_from_many_threads(self, runtime: Taskport) -> None:
        """A runtime is shared across a web server's threads; it must hold up."""
        results: list[int] = []
        errors: list[BaseException] = []

        def worker(n: int) -> None:
            try:
                results.append(runtime.inline.run(tasks_fixture.add, n, n))
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors
        assert sorted(results) == sorted(i * 2 for i in range(24))

    def test_a_backend_is_built_exactly_once_under_contention(self) -> None:
        built: list[int] = []

        def factory(**options: object) -> object:
            from taskport.backends.inline import InlineExecutionBackend

            built.append(1)
            return InlineExecutionBackend()

        from taskport import register_backend, unregister_backend

        register_backend("once", factory, replace=True)
        try:
            rt = Taskport(
                config=TaskportConfig(
                    backends={"o": BackendConfig(factory="once")},
                    defaults={ExecutionKind.INLINE: "o"},
                )
            )
            threads = [threading.Thread(target=lambda: rt.backend("o")) for _ in range(16)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            assert sum(built) == 1
            rt.close()
        finally:
            unregister_backend("once")


class TestBackendOptionsPortability:
    def test_options_for_another_engine_are_ignored(self, runtime: Taskport) -> None:
        """One spec must travel unchanged from one engine to another."""
        from taskport import BackendOptions

        handle = runtime.tasks.submit(
            tasks_fixture.add,
            1,
            2,
            backend_options=BackendOptions({"procrastinate": {"lock": "irrelevant-here"}}),
        )
        assert handle.result(10).value == 3


class TestDeferredRoutingExample:
    """The worked example from the brief: same code, different engines."""

    def test_one_call_site_two_destinations(self) -> None:
        config = TaskportConfig.from_mapping(
            {
                "backends": {
                    "fast": {"factory": "thread", "max_workers": 2},
                    "isolated": {"factory": "process", "max_workers": 1},
                },
                "routes": [
                    {"kind": "task", "queue": "metadata", "backend": "fast"},
                    {"kind": "task", "queue": "heavy", "backend": "isolated"},
                ],
                "defaults": {"task": "fast"},
            }
        )
        rt = Taskport(config=config)
        try:
            here = rt.tasks.submit(tasks_fixture.pid, queue="metadata")
            elsewhere = rt.tasks.submit(tasks_fixture.pid, queue="heavy")

            import os

            assert here.result(20).value == os.getpid()
            assert elsewhere.result(30).value != os.getpid(), (
                "the 'heavy' queue routes to a process pool, so the work must run elsewhere"
            )
        finally:
            rt.close()

    def test_the_application_never_names_a_provider(self) -> None:
        """`queue="metadata"` is domain vocabulary; the deployment maps it."""
        spec = TaskSpec(task="tasks_fixture:add", queue="metadata")
        assert "procrastinate" not in repr(spec)
        assert "thread" not in repr(spec)
