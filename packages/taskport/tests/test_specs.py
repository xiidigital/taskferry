"""Specs: what can be expressed, what is rejected, and where the line sits.

The theme running through these tests is that a spec fails *at the call site*,
in the caller's stack trace, rather than on a worker an hour later where the
traceback has no useful context left in it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from taskport import (
    BackendOptions,
    Capability,
    ExecutionKind,
    InlineSpec,
    JobSpec,
    Resources,
    RetryOwner,
    RetryPolicy,
    SerializationError,
    TaskSpec,
    TimeoutPolicy,
)
from taskport.errors import FunctionResolutionError


class TestTaskSpec:
    def test_validates_json_arguments_at_construction(self) -> None:
        """A non-JSON argument must fail here, not on a worker."""
        with pytest.raises(SerializationError):
            TaskSpec(task="pkg.mod:fn", args=(object(),))

    def test_rejects_a_malformed_function_reference(self) -> None:
        with pytest.raises(FunctionResolutionError):
            TaskSpec(task="not-a-reference")

    def test_rejects_delay_and_run_at_together(self) -> None:
        """Two different answers to "when" is a bug, not a merge."""
        with pytest.raises(ValueError, match="not both"):
            TaskSpec(
                task="pkg.mod:fn",
                delay=timedelta(seconds=10),
                run_at=datetime.now(UTC),
            )

    def test_rejects_a_negative_delay(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            TaskSpec(task="pkg.mod:fn", delay=timedelta(seconds=-1))

    def test_name_defaults_to_the_task_path(self) -> None:
        assert TaskSpec(task="pkg.mod:fn").name == "pkg.mod:fn"

    def test_kind_is_task(self) -> None:
        assert TaskSpec(task="pkg.mod:fn").kind is ExecutionKind.TASK

    def test_scheduled_for_computes_from_an_injected_now(self) -> None:
        """The clock is a parameter, so scheduling is testable without freezing time."""
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        spec = TaskSpec(task="pkg.mod:fn", delay=timedelta(minutes=5))
        assert spec.scheduled_for(now=now) == datetime(2026, 1, 1, 12, 5, tzinfo=UTC)

    def test_scheduled_for_is_none_when_immediate(self) -> None:
        assert TaskSpec(task="pkg.mod:fn").scheduled_for() is None

    def test_is_immutable(self) -> None:
        spec = TaskSpec(task="pkg.mod:fn")
        with pytest.raises(AttributeError):
            spec.queue = "other"  # type: ignore[misc]

    def test_evolve_returns_a_copy(self) -> None:
        original = TaskSpec(task="pkg.mod:fn", queue="a")
        changed = original.evolve(queue="b")
        assert original.queue == "a"
        assert changed.queue == "b"
        assert changed.task == original.task

    def test_mutable_arguments_are_frozen(self) -> None:
        """A caller mutating their dict afterwards must not change the spec."""
        payload = {"key": "value"}
        spec = TaskSpec(task="pkg.mod:fn", kwargs=payload)
        payload["key"] = "changed"
        assert spec.kwargs["key"] == "value"


class TestRequiredCapabilities:
    """A spec states what it needs; a backend is checked against it before submit."""

    def test_a_plain_task_needs_only_submit(self) -> None:
        assert TaskSpec(task="pkg.mod:fn").required_capabilities() == frozenset({Capability.SUBMIT})

    def test_a_deferred_task_needs_delay(self) -> None:
        spec = TaskSpec(task="pkg.mod:fn", delay=timedelta(seconds=30))
        assert Capability.DELAY in spec.required_capabilities()

    def test_a_prioritised_task_needs_priority(self) -> None:
        spec = TaskSpec(task="pkg.mod:fn", priority=5)
        assert Capability.PRIORITY in spec.required_capabilities()

    def test_an_idempotency_key_needs_deduplication(self) -> None:
        spec = TaskSpec(task="pkg.mod:fn", idempotency_key="abc")
        assert Capability.DEDUPLICATION in spec.required_capabilities()

    def test_an_application_owned_retry_asks_nothing_of_the_backend(self) -> None:
        """If the caller retries, the engine must not be asked to as well."""
        spec = TaskSpec(
            task="pkg.mod:fn",
            retry=RetryPolicy(max_attempts=3, owner=RetryOwner.APPLICATION),
        )
        assert Capability.RETRY not in spec.required_capabilities()

    def test_a_backend_owned_retry_needs_retry(self) -> None:
        spec = TaskSpec(task="pkg.mod:fn", retry=RetryPolicy(max_attempts=3))
        assert Capability.RETRY in spec.required_capabilities()

    def test_a_gpu_job_needs_gpu(self) -> None:
        spec = JobSpec(job="train", resources=Resources(gpu=2))
        assert Capability.GPU in spec.required_capabilities()

    def test_an_array_job_needs_parallelism(self) -> None:
        assert Capability.PARALLELISM in JobSpec(job="j", parallelism=4).required_capabilities()

    def test_a_timeout_needs_timeout(self) -> None:
        spec = JobSpec(job="j", timeout=TimeoutPolicy(seconds=60))
        assert Capability.TIMEOUT in spec.required_capabilities()


class TestJobSpec:
    def test_requires_a_job_name(self) -> None:
        with pytest.raises(ValueError, match="required"):
            JobSpec(job="")

    def test_rejects_parallelism_below_one(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            JobSpec(job="j", parallelism=0)

    def test_argv_is_command_then_args(self) -> None:
        """Kept separate because several runtimes model entrypoint and args apart."""
        spec = JobSpec(job="j", command=["python", "etl.py"], args=["--verbose"])
        assert spec.argv == ("python", "etl.py", "--verbose")

    def test_kind_is_job(self) -> None:
        assert JobSpec(job="j").kind is ExecutionKind.JOB

    def test_does_not_validate_arguments_as_json(self) -> None:
        """A job's argv is strings; there is no Python payload to serialize."""
        assert JobSpec(job="j", command=["echo", "hello"]).argv == ("echo", "hello")


class TestResources:
    def test_rejects_a_gpu_type_without_a_gpu_count(self) -> None:
        """Asking for a T4 and zero GPUs is a mistake worth catching."""
        with pytest.raises(ValueError, match="gpu count is 0"):
            Resources(gpu_type="nvidia-tesla-t4")

    def test_rejects_a_negative_gpu_count(self) -> None:
        with pytest.raises(ValueError, match=">= 0"):
            Resources(gpu=-1)

    def test_is_empty_when_nothing_is_requested(self) -> None:
        assert Resources().is_empty
        assert not Resources(cpu="1000m").is_empty


class TestBackendOptions:
    """The escape hatch: provider vocabulary, namespaced, never in the core fields."""

    def test_each_backend_reads_only_its_own_namespace(self) -> None:
        spec = TaskSpec(
            task="pkg.mod:fn",
            backend_options=BackendOptions(
                {
                    "procrastinate": {"lock": "meta-42"},
                    "cloudtasks": {"dispatch_deadline": 900},
                }
            ),
        )
        assert spec.options_for("procrastinate") == {"lock": "meta-42"}
        assert spec.options_for("cloudtasks") == {"dispatch_deadline": 900}

    def test_an_unknown_backend_sees_an_empty_mapping(self) -> None:
        """This is what lets one spec travel unchanged between engines."""
        spec = TaskSpec(
            task="pkg.mod:fn",
            backend_options=BackendOptions({"procrastinate": {"lock": "x"}}),
        )
        assert spec.options_for("cloudtasks") == {}

    def test_no_provider_vocabulary_leaks_into_spec_fields(self) -> None:
        """The regression guard for the design rule in section 12 of the brief."""
        for spec_type in (TaskSpec, JobSpec):
            fields = set(spec_type.__dataclass_fields__)
            leaked = {
                field
                for field in fields
                for vendor in ("procrastinate", "celery", "cloud_tasks", "cloud_run", "kubernetes")
                if vendor in field
            }
            assert not leaked, f"{spec_type.__name__} has provider-specific fields: {leaked}"


class TestInlineSpec:
    def test_accepts_a_lambda(self) -> None:
        """The one spec that may hold a live object: nothing crosses a boundary."""
        spec = InlineSpec(func=lambda: 42)
        assert spec.func() == 42

    def test_rejects_a_non_callable(self) -> None:
        with pytest.raises(TypeError, match="must be callable"):
            InlineSpec(func="not callable")  # type: ignore[arg-type]

    def test_name_defaults_to_the_callables_qualname(self) -> None:
        def my_function() -> None: ...

        assert "my_function" in InlineSpec(func=my_function).name
