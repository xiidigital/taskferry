"""The Dramatiq adapter, tested with a fake actor and no broker."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from taskferry import BackendOptions, Capability, ExecutionState, RetryPolicy, TaskSpec
from taskferry.contract import TaskBackendContract
from taskferry.envelope import ENVELOPE_VERSION
from taskferry.errors import ConfigurationError, SubmissionError, UnsupportedCapability
from taskferry.functions import FunctionRegistry
from taskferry.ports import ExecutionBackend
from taskferry_dramatiq import DramatiqTaskBackend, dispatch


class FakeActor:
    """The whole Dramatiq surface this adapter touches. Deliberately small."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self.fail = fail

    def send_with_options(self, **options: Any) -> SimpleNamespace:
        if self.fail:
            raise RuntimeError("ConnectionError: redis is down")
        self.sent.append(options)
        return SimpleNamespace(message_id=f"dq-{len(self.sent)}")


def backend(**overrides: Any) -> DramatiqTaskBackend:
    return DramatiqTaskBackend(actor=overrides.pop("actor", FakeActor()), **overrides)


class TestDramatiqContract(TaskBackendContract):
    def make_backend(self) -> ExecutionBackend:
        return backend()

    def success_spec(self) -> TaskSpec:
        return TaskSpec(task="myapp.tasks:send_email", args=(42,))


class TestSending:
    def test_the_envelope_is_the_actor_argument(self) -> None:
        actor = FakeActor()
        backend(actor=actor).submit(TaskSpec(task="myapp:send", args=(42,), kwargs={"cc": "a"}))
        envelope = actor.sent[0]["args"][0]
        assert envelope["taskferry"] == ENVELOPE_VERSION
        assert envelope["task"] == "myapp:send"
        assert envelope["args"] == [42]
        assert envelope["kwargs"] == {"cc": "a"}

    def test_the_queue_reaches_dramatiq(self) -> None:
        actor = FakeActor()
        backend(actor=actor).submit(TaskSpec(task="m:f", queue="email"))
        assert actor.sent[0]["queue_name"] == "email"

    def test_a_delay_becomes_milliseconds(self) -> None:
        actor = FakeActor()
        backend(actor=actor).submit(TaskSpec(task="m:f", delay=timedelta(seconds=90)))
        assert 89_000 <= actor.sent[0]["delay"] <= 90_000

    def test_an_immediate_task_sets_no_delay(self) -> None:
        actor = FakeActor()
        backend(actor=actor).submit(TaskSpec(task="m:f"))
        assert "delay" not in actor.sent[0]

    def test_retry_attempts_become_max_retries(self) -> None:
        """Dramatiq counts retries; RetryPolicy counts attempts. Off by one, on purpose."""
        actor = FakeActor()
        backend(actor=actor).submit(TaskSpec(task="m:f", retry=RetryPolicy(max_attempts=4)))
        assert actor.sent[0]["max_retries"] == 3

    def test_backend_options_pass_through(self) -> None:
        actor = FakeActor()
        backend(actor=actor).submit(
            TaskSpec(
                task="m:f",
                backend_options=BackendOptions({"dramatiq": {"time_limit": 60_000}}),
            )
        )
        assert actor.sent[0]["time_limit"] == 60_000

    def test_another_engines_options_are_ignored(self) -> None:
        actor = FakeActor()
        backend(actor=actor).submit(
            TaskSpec(
                task="m:f",
                backend_options=BackendOptions({"procrastinate": {"lock": "irrelevant"}}),
            )
        )
        assert "lock" not in actor.sent[0]

    def test_the_message_id_is_kept_as_the_external_id(self) -> None:
        execution = backend().submit(TaskSpec(task="m:f"))
        assert execution.id.startswith("task_")
        assert execution.external_id == "dq-1"
        assert execution.state is ExecutionState.QUEUED

    def test_a_send_failure_becomes_a_submission_error(self) -> None:
        with pytest.raises(SubmissionError) as caught:
            backend(actor=FakeActor(fail=True)).submit(TaskSpec(task="m:f"))
        assert isinstance(caught.value.__cause__, RuntimeError)


class TestActorResolution:
    def test_a_missing_actor_says_exactly_what_to_do(self) -> None:
        target = DramatiqTaskBackend()
        with pytest.raises(ConfigurationError, match="build_dispatch_actor"):
            target.submit(TaskSpec(task="m:f"))

    def test_a_missing_actor_is_not_reported_as_a_submission_failure(self) -> None:
        """A configuration problem must not send you to look at Redis."""
        target = DramatiqTaskBackend()
        with pytest.raises(ConfigurationError):
            target.submit(TaskSpec(task="m:f"))

    def test_a_dotted_path_is_resolved_lazily(self) -> None:
        """So the backend is constructible before the broker is connected."""
        target = DramatiqTaskBackend(actor="taskferry_dramatiq:__version__")
        assert target.actor() is not None

    def test_an_unresolvable_path_is_a_configuration_error(self) -> None:
        target = DramatiqTaskBackend(actor="nonexistent.module:actor")
        with pytest.raises(ConfigurationError, match="cannot resolve"):
            target.actor()

    def test_an_actor_can_come_from_a_broker(self) -> None:
        actor = FakeActor()
        broker = SimpleNamespace(actors={"taskferry_execute": actor})
        assert DramatiqTaskBackend(broker=broker).actor() is actor

    def test_a_broker_without_the_actor_says_so(self) -> None:
        broker = SimpleNamespace(actors={})
        target = DramatiqTaskBackend(broker=broker)
        with pytest.raises(ConfigurationError, match="no actor named"):
            target.actor()


class TestCapabilitiesNextToProcrastinate:
    """The two worker-based engines, and the differences stated honestly."""

    def test_delay_and_retry_are_advertised(self) -> None:
        caps = backend().capabilities
        assert Capability.DELAY in caps
        assert Capability.RETRY in caps

    def test_state_cancel_priority_and_deduplication_are_absent(self) -> None:
        caps = backend().capabilities
        for absent in (
            Capability.STATE,
            Capability.CANCEL,
            Capability.PRIORITY,
            Capability.DEDUPLICATION,
            Capability.RESULT,
        ):
            assert absent not in caps

    def test_it_differs_from_procrastinate_where_the_engines_differ(self) -> None:
        from taskferry_procrastinate import ProcrastinateTaskBackend

        dramatiq = backend().capabilities
        procrastinate = ProcrastinateTaskBackend(app=object()).capabilities

        # Both are worker-based engines that defer and retry...
        for shared in (Capability.SUBMIT, Capability.DELAY, Capability.RETRY):
            assert shared in dramatiq and shared in procrastinate

        # ...and only one of them can look a job up or cancel it.
        for pg_only in (Capability.STATE, Capability.CANCEL, Capability.PRIORITY):
            assert pg_only in procrastinate
            assert pg_only not in dramatiq

    def test_asking_for_state_is_refused(self) -> None:
        target = backend()
        execution = target.submit(TaskSpec(task="m:f"))
        with pytest.raises(UnsupportedCapability):
            target.get(execution.id)

    def test_a_prioritised_spec_is_refused(self) -> None:
        with pytest.raises(UnsupportedCapability, match="priority"):
            backend().submit(TaskSpec(task="m:f", priority=5))


class TestWorker:
    def test_dispatch_resolves_and_runs(self) -> None:
        registry = FunctionRegistry()
        registry.register(lambda a, b: a + b, name="tests:add")
        envelope = {"taskferry": ENVELOPE_VERSION, "task": "tests:add", "args": [20, 22]}
        assert dispatch(envelope, registry=registry) == 42

    def test_an_allowlist_blocks_an_arbitrary_import(self) -> None:
        from taskferry.errors import FunctionResolutionError

        registry = FunctionRegistry(allowed_modules=["myapp"])
        envelope = {"taskferry": ENVELOPE_VERSION, "task": "os:system", "args": ["echo pwned"]}
        with pytest.raises(FunctionResolutionError, match="allowlist"):
            dispatch(envelope, registry=registry)

    def test_a_version_mismatch_is_rejected(self) -> None:
        from taskferry.errors import SerializationError

        with pytest.raises(SerializationError, match="unsupported taskferry envelope version"):
            dispatch({"taskferry": "99", "task": "tests:add"})

    def test_correlation_survives_the_round_trip(self) -> None:
        from taskferry.core.correlation import Correlation, current_correlation
        from taskferry.envelope import build_envelope

        seen: list[str] = []
        registry = FunctionRegistry()
        registry.register(
            lambda: seen.append(current_correlation().correlation_id),  # type: ignore[union-attr]
            name="tests:observe",
        )
        correlation = Correlation.start()
        envelope = build_envelope(TaskSpec(task="tests:observe", correlation=correlation))
        dispatch(envelope, registry=registry)
        assert seen == [correlation.correlation_id]
