"""The Procrastinate adapter, tested with a fake App and no PostgreSQL.

Procrastinate is not installed in this environment, on purpose: it proves the
adapter's *own* code is exercised, and that ``taskferry`` needs neither
Procrastinate nor a database to be tested. The fake below implements only the
narrow surface the adapter uses, which is itself a useful check — an adapter that
needed a large fake would be reaching too far into the engine.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from taskferry import BackendOptions, Capability, ExecutionState, TaskSpec
from taskferry.contract import TaskBackendContract
from taskferry.errors import ConfigurationError, ExecutionNotFound, SubmissionError
from taskferry.functions import FunctionRegistry
from taskferry.ports import ExecutionBackend
from taskferry_procrastinate import ProcrastinateTaskBackend, map_status
from taskferry_procrastinate.message import (
    MESSAGE_VERSION,
    build_message,
    check_version,
    decode_retry,
    encode_retry,
)
from taskferry_procrastinate.worker import execute_message


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeJob:
    def __init__(self, job_id: int, status: str = "todo", queue: str = "default") -> None:
        self.id = job_id
        self.status = status
        self.queue_name = queue
        self.task_name = "taskferry:dispatch"
        self.attempts = 0


class FakeJobManager:
    def __init__(self) -> None:
        self.jobs: dict[int, FakeJob] = {}
        self.cancelled: list[int] = []

    def list_jobs(self, *, id: int | None = None, **_: Any) -> list[FakeJob]:
        job = self.jobs.get(id) if id is not None else None
        return [job] if job is not None else []

    def cancel_job_by_id(self, job_id: int) -> None:
        self.cancelled.append(job_id)
        if job_id in self.jobs:
            self.jobs[job_id].status = "cancelled"


class FakeDeferrer:
    def __init__(self, app: FakeApp, kwargs: dict[str, Any]) -> None:
        self._app = app
        self._kwargs = kwargs

    def defer(self, **payload: Any) -> int:
        if self._app.fail_on_defer:
            raise RuntimeError("connection to PostgreSQL refused")
        job_id = self._app.next_id
        self._app.next_id += 1
        self._app.deferred.append({**self._kwargs, **payload, "id": job_id})
        self._app.job_manager.jobs[job_id] = FakeJob(job_id, queue=self._kwargs.get("queue", "d"))
        return job_id


class FakeApp:
    """The whole Procrastinate surface this adapter touches. Deliberately small."""

    def __init__(self, *, fail_on_defer: bool = False) -> None:
        self.deferred: list[dict[str, Any]] = []
        self.job_manager = FakeJobManager()
        self.next_id = 4711
        self.fail_on_defer = fail_on_defer
        self.registered: dict[str, Any] = {}

    def configure_task(self, **kwargs: Any) -> FakeDeferrer:
        return FakeDeferrer(self, kwargs)

    def task(self, *, name: str, **options: Any) -> Any:
        def decorator(func: Any) -> Any:
            self.registered[name] = (func, options)
            return func

        return decorator


@pytest.fixture
def app() -> FakeApp:
    return FakeApp()


@pytest.fixture
def backend(app: FakeApp) -> ProcrastinateTaskBackend:
    return ProcrastinateTaskBackend(app=app)


# --------------------------------------------------------------------------- #
# Contract
# --------------------------------------------------------------------------- #
class TestProcrastinateContract(TaskBackendContract):
    """Same suite every other task backend runs. That is the point."""

    def make_backend(self) -> ExecutionBackend:
        return ProcrastinateTaskBackend(app=FakeApp())

    def success_spec(self) -> TaskSpec:
        return TaskSpec(task="tests.tasks:ok", args=(1, 2))


# --------------------------------------------------------------------------- #
# Translation
# --------------------------------------------------------------------------- #
class TestSpecTranslation:
    def test_queue_and_priority_reach_the_engine(
        self, backend: ProcrastinateTaskBackend, app: FakeApp
    ) -> None:
        backend.submit(TaskSpec(task="myapp.tasks:reindex", queue="metadata", priority=7))
        assert app.deferred[0]["queue"] == "metadata"
        assert app.deferred[0]["priority"] == 7

    def test_a_delay_becomes_schedule_at(
        self, backend: ProcrastinateTaskBackend, app: FakeApp
    ) -> None:
        backend.submit(TaskSpec(task="m:f", delay=timedelta(minutes=5)))
        assert app.deferred[0]["schedule_at"] is not None

    def test_an_idempotency_key_becomes_a_queueing_lock(
        self, backend: ProcrastinateTaskBackend, app: FakeApp
    ) -> None:
        """Procrastinate's real deduplication mechanism, not an invented one."""
        backend.submit(TaskSpec(task="m:f", idempotency_key="reindex-42"))
        assert app.deferred[0]["queueing_lock"] == "reindex-42"

    def test_backend_options_pass_through(
        self, backend: ProcrastinateTaskBackend, app: FakeApp
    ) -> None:
        backend.submit(
            TaskSpec(
                task="m:f",
                backend_options=BackendOptions({"procrastinate": {"lock": "row-42"}}),
            )
        )
        assert app.deferred[0]["lock"] == "row-42"

    def test_another_engines_options_are_ignored(
        self, backend: ProcrastinateTaskBackend, app: FakeApp
    ) -> None:
        """One spec must survive a move between engines untouched."""
        backend.submit(
            TaskSpec(
                task="m:f",
                backend_options=BackendOptions({"cloudtasks": {"dispatch_deadline": 900}}),
            )
        )
        assert "dispatch_deadline" not in app.deferred[0]

    def test_the_taskferry_id_is_not_the_procrastinate_id(
        self, backend: ProcrastinateTaskBackend
    ) -> None:
        execution = backend.submit(TaskSpec(task="m:f"))
        assert execution.id.startswith("task_")
        assert execution.external_id == "4711"

    def test_a_defer_failure_becomes_a_submission_error(self) -> None:
        """Provider exceptions never reach the caller as provider exceptions."""
        backend = ProcrastinateTaskBackend(app=FakeApp(fail_on_defer=True))
        with pytest.raises(SubmissionError) as caught:
            backend.submit(TaskSpec(task="m:f"))
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert caught.value.backend == "procrastinate"


class TestStateMapping:
    @pytest.mark.parametrize(
        ("procrastinate_status", "expected"),
        [
            ("todo", ExecutionState.QUEUED),
            ("doing", ExecutionState.RUNNING),
            ("succeeded", ExecutionState.SUCCEEDED),
            ("failed", ExecutionState.FAILED),
            ("cancelled", ExecutionState.CANCELLED),
            ("aborting", ExecutionState.RUNNING),
            ("aborted", ExecutionState.CANCELLED),
        ],
    )
    def test_known_statuses_map(self, procrastinate_status: str, expected: ExecutionState) -> None:
        assert map_status(procrastinate_status) == expected

    def test_an_unknown_status_becomes_unknown_not_a_guess(self) -> None:
        """A future Procrastinate status must not silently become "succeeded"."""
        assert map_status("some_future_status") is ExecutionState.UNKNOWN
        assert map_status(None) is ExecutionState.UNKNOWN

    def test_an_enum_like_status_is_read_by_value(self) -> None:
        class Status:
            value = "succeeded"

        assert map_status(Status()) is ExecutionState.SUCCEEDED

    def test_get_reads_the_engines_state(
        self, backend: ProcrastinateTaskBackend, app: FakeApp
    ) -> None:
        execution = backend.submit(TaskSpec(task="m:f"))
        app.job_manager.jobs[4711].status = "doing"
        assert backend.get(execution.id).state is ExecutionState.RUNNING

    def test_get_accepts_a_bare_procrastinate_id(
        self, backend: ProcrastinateTaskBackend, app: FakeApp
    ) -> None:
        """An operator reading a job id off a dashboard can look it up anywhere."""
        backend.submit(TaskSpec(task="m:f"))
        assert backend.get("4711").state is ExecutionState.QUEUED

    def test_an_unknown_id_explains_the_cross_process_limitation(
        self, backend: ProcrastinateTaskBackend
    ) -> None:
        with pytest.raises(ExecutionNotFound, match="another process"):
            backend.get("task_never_submitted_here")


class TestCapabilities:
    def test_result_is_not_advertised(self, backend: ProcrastinateTaskBackend) -> None:
        """Procrastinate stores no return values, and this says so."""
        assert Capability.RESULT not in backend.capabilities

    def test_asking_for_a_result_is_refused(self, backend: ProcrastinateTaskBackend) -> None:
        from taskferry.errors import UnsupportedCapability

        execution = backend.submit(TaskSpec(task="m:f"))
        with pytest.raises(UnsupportedCapability):
            backend.result(execution.id)

    def test_the_engine_capabilities_are_advertised(
        self, backend: ProcrastinateTaskBackend
    ) -> None:
        for capability in (
            Capability.DELAY,
            Capability.PRIORITY,
            Capability.RETRY,
            Capability.DEDUPLICATION,
            Capability.STATE,
            Capability.CANCEL,
        ):
            assert capability in backend.capabilities

    def test_cancel_goes_through_the_job_manager(
        self, backend: ProcrastinateTaskBackend, app: FakeApp
    ) -> None:
        execution = backend.submit(TaskSpec(task="m:f"))
        cancelled = backend.cancel(execution.id)
        assert app.job_manager.cancelled == [4711]
        assert cancelled.state is ExecutionState.CANCELLED


class TestConfiguration:
    def test_a_missing_app_says_exactly_what_to_provide(self) -> None:
        backend = ProcrastinateTaskBackend()
        with pytest.raises(ConfigurationError, match=r"app='myapp\.tasks:app'"):
            backend.submit(TaskSpec(task="m:f"))

    def test_a_dotted_app_path_is_resolved_lazily(self) -> None:
        """A backend must be constructible before the database is configured."""
        backend = ProcrastinateTaskBackend(app="taskferry_procrastinate:__version__")
        assert backend.app() is not None

    def test_an_unresolvable_app_path_is_a_configuration_error(self) -> None:
        backend = ProcrastinateTaskBackend(app="nonexistent.module:app")
        with pytest.raises(ConfigurationError, match="cannot resolve"):
            backend.app()


# --------------------------------------------------------------------------- #
# The wire envelope and the worker side
# --------------------------------------------------------------------------- #
class TestMessage:
    def test_the_envelope_is_json_shaped(self) -> None:
        import json

        message = build_message(TaskSpec(task="m:f", args=(1, "two"), kwargs={"k": [1, 2]}))
        assert json.loads(json.dumps(message)) == message

    def test_an_unknown_version_is_rejected_loudly(self) -> None:
        """A worker on old code must not silently drop fields it does not know."""
        with pytest.raises(ValueError, match="unsupported taskferry envelope version"):
            check_version({"taskferry": "99", "task": "m:f"})

    def test_a_non_object_body_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be a JSON object"):
            check_version(["not", "an", "object"])

    def test_the_current_version_passes(self) -> None:
        check_version({"taskferry": MESSAGE_VERSION, "task": "m:f"})

    def test_the_envelope_is_the_shared_one(self) -> None:
        """One wire format across every transport, not one per adapter."""
        from taskferry.envelope import ENVELOPE_VERSION, build_envelope

        assert MESSAGE_VERSION == ENVELOPE_VERSION
        spec = TaskSpec(task="m:f", args=(1,))
        assert build_message(spec) == build_envelope(spec)

    def test_the_retry_policy_round_trips(self) -> None:
        from taskferry import Backoff, RetryPolicy

        policy = RetryPolicy(max_attempts=5, backoff=Backoff.LINEAR, initial_delay=2.0)
        restored = decode_retry({"retry": encode_retry(policy)})
        assert restored.max_attempts == 5
        assert restored.backoff is Backoff.LINEAR
        assert restored.initial_delay == 2.0

    def test_no_retry_encodes_to_nothing(self) -> None:
        from taskferry.retry import NO_RETRY

        assert encode_retry(NO_RETRY) is None


class TestWorker:
    def test_a_message_resolves_and_runs_its_function(self) -> None:
        registry = FunctionRegistry()
        registry.register(lambda a, b: a + b, name="tests:add")
        result = execute_message(
            {"taskferry": MESSAGE_VERSION, "task": "tests:add", "args": [20, 22]},
            registry=registry,
        )
        assert result == 42

    def test_an_allowlist_blocks_an_unregistered_module(self) -> None:
        """Task names arrive from the queue; an open resolver is an RCE primitive."""
        from taskferry.errors import FunctionResolutionError

        registry = FunctionRegistry(allowed_modules=["myapp"])
        with pytest.raises(FunctionResolutionError, match="not in the import allowlist"):
            execute_message(
                {"taskferry": MESSAGE_VERSION, "task": "os:system", "args": ["echo pwned"]},
                registry=registry,
            )

    def test_a_message_without_a_task_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="no 'task'"):
            execute_message({"taskferry": MESSAGE_VERSION})

    def test_the_dispatcher_registers_under_the_expected_name(self, app: FakeApp) -> None:
        from taskferry_procrastinate import register_dispatcher

        register_dispatcher(app)
        assert "taskferry:dispatch" in app.registered

    def test_correlation_survives_the_round_trip(self) -> None:
        from taskferry.core.correlation import Correlation, current_correlation

        seen: list[str | None] = []
        registry = FunctionRegistry()
        registry.register(
            lambda: seen.append(
                current_correlation().correlation_id if current_correlation() else None
            ),
            name="tests:observe",
        )
        correlation = Correlation.start()
        message = build_message(TaskSpec(task="tests:observe", correlation=correlation))
        execute_message(message, registry=registry)
        assert seen == [correlation.correlation_id]
