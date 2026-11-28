"""The Celery adapter, tested with a fake app and no broker.

Celery is not installed in this environment, on purpose: it proves the adapter's
own code is exercised, and that ``taskferry`` needs neither Celery nor a broker to
be tested. The fake implements only the narrow surface the adapter touches.
"""

from __future__ import annotations

import tomllib
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import taskferry_celery
from taskferry import (
    BackendOptions,
    Backoff,
    Capability,
    ExecutionState,
    RetryOwner,
    RetryPolicy,
    TaskSpec,
)
from taskferry.contract import TaskBackendContract
from taskferry.errors import ConfigurationError, ExecutionNotFound, SubmissionError
from taskferry.functions import FunctionRegistry
from taskferry.ports import ExecutionBackend
from taskferry_celery import CeleryTaskBackend, map_state
from taskferry_celery.message import MESSAGE_VERSION, build_message, check_version
from taskferry_celery.worker import execute_message


def test_package_version_matches_distribution_metadata() -> None:
    from pathlib import Path

    project_file = Path(__file__).parents[1] / "pyproject.toml"
    project = tomllib.loads(project_file.read_text())
    assert taskferry_celery.__version__ == project["project"]["version"]


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeAsyncResult:
    def __init__(self, task_id: str, state: str = "PENDING", value: Any = None) -> None:
        self.id = task_id
        self.state = state
        self.revoked = False
        self.value = value
        self.traceback = None

    def get(self, *, timeout: float | None = None, propagate: bool = True) -> Any:
        return self.value

    def revoke(self) -> None:
        self.revoked = True
        self.state = "REVOKED"


class FakeCelery:
    """The whole Celery surface this adapter touches. Deliberately small."""

    def __init__(self, *, fail_on_send: bool = False, result_backend: str = "") -> None:
        self.sent: list[dict[str, Any]] = []
        self.results: dict[str, FakeAsyncResult] = {}
        self.registered: dict[str, Any] = {}
        self.fail_on_send = fail_on_send
        self.conf = SimpleNamespace(result_backend=result_backend)
        self._n = 4711

    def send_task(
        self, name: str, kwargs: dict[str, Any] | None = None, **opts: Any
    ) -> FakeAsyncResult:
        if self.fail_on_send:
            raise RuntimeError("broker connection refused")
        task_id = f"11111111-1111-1111-1111-{self._n:012d}"
        self._n += 1
        self.sent.append({"name": name, "kwargs": kwargs, **opts, "id": task_id})
        result = FakeAsyncResult(task_id)
        self.results[task_id] = result
        return result

    def AsyncResult(self, task_id: str) -> FakeAsyncResult:
        return self.results.setdefault(task_id, FakeAsyncResult(task_id))

    def task(self, *, name: str, **options: Any) -> Any:
        def decorator(func: Any) -> Any:
            self.registered[name] = (func, options)
            return func

        return decorator


@pytest.fixture
def app() -> FakeCelery:
    return FakeCelery()


@pytest.fixture
def backend(app: FakeCelery) -> CeleryTaskBackend:
    return CeleryTaskBackend(app=app)


# --------------------------------------------------------------------------- #
# Contract — the same suite every task backend runs
# --------------------------------------------------------------------------- #
class TestCeleryContract(TaskBackendContract):
    def make_backend(self) -> ExecutionBackend:
        return CeleryTaskBackend(app=FakeCelery())

    def success_spec(self) -> TaskSpec:
        return TaskSpec(task="tests.tasks:ok", args=(1, 2))


# --------------------------------------------------------------------------- #
# Translation
# --------------------------------------------------------------------------- #
class TestSpecTranslation:
    def test_queue_and_priority_reach_celery(
        self, backend: CeleryTaskBackend, app: FakeCelery
    ) -> None:
        backend.submit(TaskSpec(task="myapp.tasks:reindex", queue="metadata", priority=7))
        assert app.sent[0]["queue"] == "metadata"
        assert app.sent[0]["priority"] == 7
        assert app.sent[0]["name"] == "taskferry:dispatch"

    def test_a_delay_becomes_an_eta(self, backend: CeleryTaskBackend, app: FakeCelery) -> None:
        backend.submit(TaskSpec(task="m:f", delay=timedelta(minutes=5)))
        assert app.sent[0]["eta"] is not None

    def test_backend_options_pass_through(
        self, backend: CeleryTaskBackend, app: FakeCelery
    ) -> None:
        backend.submit(
            TaskSpec(task="m:f", backend_options=BackendOptions({"celery": {"expires": 300}}))
        )
        assert app.sent[0]["expires"] == 300

    def test_another_engines_options_are_ignored(
        self, backend: CeleryTaskBackend, app: FakeCelery
    ) -> None:
        backend.submit(
            TaskSpec(task="m:f", backend_options=BackendOptions({"procrastinate": {"lock": "x"}}))
        )
        assert "lock" not in app.sent[0]

    def test_the_message_carries_the_task_name(
        self, backend: CeleryTaskBackend, app: FakeCelery
    ) -> None:
        backend.submit(TaskSpec(task="myapp.tasks:reindex", args=(1,)))
        assert app.sent[0]["kwargs"]["message"]["task"] == "myapp.tasks:reindex"

    def test_the_taskferry_id_is_not_the_celery_id(self, backend: CeleryTaskBackend) -> None:
        execution = backend.submit(TaskSpec(task="m:f"))
        assert execution.id.startswith("task_")
        assert execution.external_id == "11111111-1111-1111-1111-000000004711"

    def test_a_send_failure_becomes_a_submission_error(self) -> None:
        backend = CeleryTaskBackend(app=FakeCelery(fail_on_send=True))
        with pytest.raises(SubmissionError) as caught:
            backend.submit(TaskSpec(task="m:f"))
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert caught.value.backend == "celery"


class TestStateMapping:
    @pytest.mark.parametrize(
        ("celery_state", "expected"),
        [
            ("PENDING", ExecutionState.QUEUED),
            ("RECEIVED", ExecutionState.QUEUED),
            ("STARTED", ExecutionState.RUNNING),
            ("RETRY", ExecutionState.RUNNING),
            ("SUCCESS", ExecutionState.SUCCEEDED),
            ("FAILURE", ExecutionState.FAILED),
            ("REVOKED", ExecutionState.CANCELLED),
        ],
    )
    def test_known_states_map(self, celery_state: str, expected: ExecutionState) -> None:
        assert map_state(celery_state) == expected

    def test_an_unknown_state_becomes_unknown_not_a_guess(self) -> None:
        assert map_state("SOME_FUTURE_STATE") is ExecutionState.UNKNOWN
        assert map_state(None) is ExecutionState.UNKNOWN

    def test_get_reads_celerys_state(self, backend: CeleryTaskBackend, app: FakeCelery) -> None:
        execution = backend.submit(TaskSpec(task="m:f"))
        app.results[execution.external_id].state = "STARTED"
        assert backend.get(execution.id).state is ExecutionState.RUNNING

    def test_get_accepts_a_bare_celery_id(
        self, backend: CeleryTaskBackend, app: FakeCelery
    ) -> None:
        backend.submit(TaskSpec(task="m:f"))
        task_id = "11111111-1111-1111-1111-000000004711"
        assert backend.get(task_id).state is ExecutionState.QUEUED

    def test_an_unknown_id_explains_the_cross_process_limitation(
        self, backend: CeleryTaskBackend
    ) -> None:
        with pytest.raises(ExecutionNotFound, match="another process"):
            backend.get("task_never_submitted_here")


class TestCapabilities:
    def test_result_is_not_advertised(self, backend: CeleryTaskBackend) -> None:
        assert Capability.RESULT not in backend.capabilities

    def test_asking_for_a_result_is_refused(self, backend: CeleryTaskBackend) -> None:
        from taskferry.errors import UnsupportedCapability

        execution = backend.submit(TaskSpec(task="m:f"))
        with pytest.raises(UnsupportedCapability):
            backend.result(execution.id)

    def test_deduplication_is_not_advertised(self, backend: CeleryTaskBackend) -> None:
        assert Capability.DEDUPLICATION not in backend.capabilities

    def test_an_idempotency_key_is_refused_not_ignored(self, backend: CeleryTaskBackend) -> None:
        from taskferry.errors import UnsupportedCapability

        with pytest.raises(UnsupportedCapability):
            backend.submit(TaskSpec(task="m:f", idempotency_key="reindex-42"))

    def test_the_engine_capabilities_are_advertised(self, backend: CeleryTaskBackend) -> None:
        for capability in (
            Capability.DELAY,
            Capability.PRIORITY,
            Capability.RETRY,
            Capability.STATE,
            Capability.CANCEL,
        ):
            assert capability in backend.capabilities

    def test_result_is_advertised_only_when_a_result_backend_is_configured(self) -> None:
        with_results = CeleryTaskBackend(app=FakeCelery(result_backend="redis://localhost/1"))
        without_results = CeleryTaskBackend(app=FakeCelery())

        assert Capability.RESULT in with_results.capabilities
        assert Capability.RESULT not in without_results.capabilities

    def test_a_configured_result_backend_returns_the_task_value(self) -> None:
        app = FakeCelery(result_backend="redis://localhost/1")
        backend = CeleryTaskBackend(app=app)
        execution = backend.submit(TaskSpec(task="m:f"))
        app.results[execution.external_id].state = "SUCCESS"
        app.results[execution.external_id].value = {"processed": 3}

        assert backend.result(execution.id).value == {"processed": 3}

    def test_a_configured_result_backend_preserves_task_failures(self) -> None:
        app = FakeCelery(result_backend="redis://localhost/1")
        backend = CeleryTaskBackend(app=app)
        execution = backend.submit(TaskSpec(task="m:f"))
        app.results[execution.external_id].state = "FAILURE"
        app.results[execution.external_id].value = RuntimeError("broken")

        outcome = backend.result(execution.id)
        assert outcome.error == "broken"
        assert outcome.error_type == "RuntimeError"

    def test_cancel_revokes_the_task(self, backend: CeleryTaskBackend, app: FakeCelery) -> None:
        execution = backend.submit(TaskSpec(task="m:f"))
        cancelled = backend.cancel(execution.id)
        assert app.results[execution.external_id].revoked is True
        assert cancelled.state is ExecutionState.CANCELLED


class TestConfiguration:
    def test_a_missing_app_says_what_to_provide(self) -> None:
        backend = CeleryTaskBackend()
        with pytest.raises(ConfigurationError, match=r"app='myapp\.celery:app'"):
            backend.submit(TaskSpec(task="m:f"))

    def test_a_dotted_app_path_is_resolved_lazily(self) -> None:
        backend = CeleryTaskBackend(app="taskferry_celery:__version__")
        assert backend.app() is not None

    def test_an_unresolvable_app_path_is_a_configuration_error(self) -> None:
        backend = CeleryTaskBackend(app="nonexistent.module:app")
        with pytest.raises(ConfigurationError, match="cannot resolve"):
            backend.app()


# --------------------------------------------------------------------------- #
# Worker side
# --------------------------------------------------------------------------- #
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
        from taskferry.errors import FunctionResolutionError

        registry = FunctionRegistry(allowed_modules=["myapp"])
        with pytest.raises(FunctionResolutionError):
            execute_message(
                {"taskferry": MESSAGE_VERSION, "task": "os:system", "args": ["echo pwned"]},
                registry=registry,
            )

    def test_bound_dispatcher_passes_the_current_celery_attempt_and_worker(self) -> None:
        from taskferry.execution_context import current_task_execution
        from taskferry_celery import register_dispatcher

        registry = FunctionRegistry()
        registry.register(
            lambda: (
                current_task_execution().attempt,
                current_task_execution().worker_id,
            ),
            name="tests:execution_context",
        )
        app = FakeCelery()
        register_dispatcher(app, registry=registry)
        dispatch, _ = app.registered["taskferry:dispatch"]
        message = build_message(TaskSpec(task="tests:execution_context"))
        request = SimpleNamespace(retries=1, hostname="worker-7")
        bound = SimpleNamespace(request=request, retry=None)

        assert dispatch(bound, message) == (2, "worker-7")

    def test_dispatcher_gives_retry_ownership_to_celery_once(self, monkeypatch) -> None:
        from taskferry_celery import register_dispatcher

        registry = FunctionRegistry()

        def fail() -> None:
            raise ConnectionError("transient")

        registry.register(fail, name="tests:fail")
        app = FakeCelery()
        dispatch = register_dispatcher(app, registry=registry)
        message = build_message(
            TaskSpec(
                task="tests:fail",
                retry=RetryPolicy(
                    max_attempts=3,
                    backoff=Backoff.FIXED,
                    initial_delay=4,
                    jitter=True,
                    retry_on=("ConnectionError",),
                ),
            )
        )
        monkeypatch.setattr("taskferry_celery.worker.random.uniform", lambda low, high: high / 2)
        calls: list[dict[str, Any]] = []

        class FakeRetry(Exception):
            pass

        def celery_retry(**kwargs: Any) -> str:
            calls.append(kwargs)
            raise FakeRetry("scheduled")

        bound = SimpleNamespace(request=SimpleNamespace(retries=0), retry=celery_retry)
        with pytest.raises(FakeRetry, match="scheduled"):
            dispatch(bound, message)
        assert len(calls) == 1
        assert isinstance(calls[0]["exc"], ConnectionError)
        assert calls[0]["countdown"] == 2
        assert calls[0]["max_retries"] == 2

    def test_dispatcher_does_not_retry_filtered_exception_or_exhausted_attempt(self) -> None:
        from taskferry_celery import register_dispatcher

        registry = FunctionRegistry()

        def value_error() -> None:
            raise ValueError("permanent")

        registry.register(value_error, name="tests:permanent")
        dispatch = register_dispatcher(FakeCelery(), registry=registry)
        message = build_message(
            TaskSpec(
                task="tests:permanent",
                retry=RetryPolicy(max_attempts=3, retry_on=("ConnectionError",)),
            )
        )
        calls: list[dict[str, Any]] = []
        bound = SimpleNamespace(
            request=SimpleNamespace(retries=0), retry=lambda **kwargs: calls.append(kwargs)
        )
        with pytest.raises(ValueError, match="permanent"):
            dispatch(bound, message)
        assert calls == []

        retrying_registry = FunctionRegistry()
        retrying_registry.register(value_error, name="tests:permanent")
        dispatch = register_dispatcher(FakeCelery(), registry=retrying_registry)
        unfiltered = build_message(
            TaskSpec(task="tests:permanent", retry=RetryPolicy(max_attempts=2))
        )
        bound.request.retries = 1
        with pytest.raises(ValueError, match="permanent"):
            dispatch(bound, unfiltered)
        assert calls == []

    def test_application_owns_retries_and_celery_is_not_reconfigured(self) -> None:
        from taskferry_celery import register_dispatcher

        registry = FunctionRegistry()

        def fail() -> None:
            raise ConnectionError("transient")

        registry.register(fail, name="tests:fail")
        dispatch = register_dispatcher(FakeCelery(), registry=registry)
        message = build_message(
            TaskSpec(
                task="tests:fail",
                retry=RetryPolicy(max_attempts=5, owner=RetryOwner.APPLICATION),
            )
        )
        calls: list[dict[str, Any]] = []
        bound = SimpleNamespace(
            request=SimpleNamespace(retries=0), retry=lambda **kwargs: calls.append(kwargs)
        )
        with pytest.raises(ConnectionError, match="transient"):
            dispatch(bound, message)
        assert calls == []

    def test_autoretry_options_are_rejected_to_prevent_a_second_retry_owner(self) -> None:
        from taskferry_celery import register_dispatcher

        with pytest.raises(ValueError, match="conflict with Taskferry"):
            register_dispatcher(FakeCelery(), autoretry_for=(Exception,))

    def test_an_unknown_version_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="unsupported taskferry envelope version"):
            check_version({"taskferry": "99", "task": "m:f"})

    def test_the_dispatcher_registers_under_the_expected_name(self, app: FakeCelery) -> None:
        from taskferry_celery import register_dispatcher

        register_dispatcher(app)
        assert "taskferry:dispatch" in app.registered
        assert app.registered["taskferry:dispatch"][1]["bind"] is True

    def test_the_envelope_is_the_shared_one(self) -> None:
        from taskferry.envelope import ENVELOPE_VERSION, build_envelope

        assert MESSAGE_VERSION == ENVELOPE_VERSION
        spec = TaskSpec(task="m:f", args=(1,))
        assert build_message(spec) == build_envelope(spec)
