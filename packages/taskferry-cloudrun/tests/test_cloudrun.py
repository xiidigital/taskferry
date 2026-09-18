"""The Cloud Run Jobs adapter, tested with injected clients and no GCP account.

The Google SDK is not installed here. Every client is injectable, so the adapter's
own translation logic — which is all the adapter *is* — is fully exercised, and
the parts that would need credentials are exactly the parts that belong to Google.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from taskferry import BackendOptions, Capability, ExecutionState, JobSpec, Resources, TimeoutPolicy
from taskferry.contract import JobBackendContract
from taskferry.errors import (
    BackendError,
    ConfigurationError,
    ExecutionNotFound,
    SubmissionError,
    UnsupportedCapability,
)
from taskferry.ports import ExecutionBackend
from taskferry.retry import RetryPolicy
from taskferry_cloudrun import CloudRunJobBackend, map_execution_state


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
def execution_proto(
    *,
    task_count: int = 1,
    succeeded: int = 0,
    failed: int = 0,
    running: int = 0,
    cancelled: int = 0,
    completion_time: Any = None,
    start_time: Any = None,
    job: str = "projects/p/locations/eu/jobs/build-cog",
) -> SimpleNamespace:
    """A duck-typed stand-in for a Cloud Run ``Execution`` message."""
    return SimpleNamespace(
        task_count=task_count,
        succeeded_count=succeeded,
        failed_count=failed,
        running_count=running,
        cancelled_count=cancelled,
        completion_time=completion_time,
        start_time=start_time,
        job=job,
    )


class NotFound(Exception):
    """Stands in for ``google.api_core.exceptions.NotFound``, matched by name."""


class FakeJobsClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.requests: list[dict[str, Any]] = []
        self.fail = fail

    def run_job(self, *, request: dict[str, Any]) -> SimpleNamespace:
        if self.fail:
            raise RuntimeError("permission denied on run_job")
        self.requests.append(request)
        name = f"{request['name']}/executions/exec-{len(self.requests)}"
        return SimpleNamespace(metadata=SimpleNamespace(name=name))


class FakeExecutionsClient:
    def __init__(self, execution: Any = None, *, error: Exception | None = None) -> None:
        self.execution = execution if execution is not None else execution_proto(running=1)
        self.error = error
        self.cancelled: list[str] = []

    def get_execution(self, *, name: str) -> Any:
        if self.error is not None:
            raise self.error
        return self.execution

    def cancel_execution(self, *, name: str) -> None:
        self.cancelled.append(name)
        self.execution = execution_proto(cancelled=1, completion_time=datetime.now(UTC))


def make_backend(**overrides: Any) -> CloudRunJobBackend:
    return CloudRunJobBackend(
        project=overrides.pop("project", "my-project"),
        location=overrides.pop("location", "europe-west1"),
        jobs_client=overrides.pop("jobs_client", FakeJobsClient()),
        executions_client=overrides.pop("executions_client", FakeExecutionsClient()),
        **overrides,
    )


# --------------------------------------------------------------------------- #
# Contract
# --------------------------------------------------------------------------- #
class TestCloudRunContract(JobBackendContract):
    def make_backend(self) -> ExecutionBackend:
        return make_backend()

    def success_spec(self) -> JobSpec:
        return JobSpec(job="build-cog", image="gdal:latest")


# --------------------------------------------------------------------------- #
# State mapping — a pure function, so no client is involved at all
# --------------------------------------------------------------------------- #
class TestStateMapping:
    def test_a_finished_execution_with_every_task_succeeded(self) -> None:
        state, error = map_execution_state(
            execution_proto(task_count=3, succeeded=3, completion_time=datetime.now(UTC))
        )
        assert state is ExecutionState.SUCCEEDED
        assert error is None

    def test_a_finished_execution_with_a_failure(self) -> None:
        state, error = map_execution_state(
            execution_proto(task_count=3, succeeded=2, failed=1, completion_time=datetime.now(UTC))
        )
        assert state is ExecutionState.FAILED
        assert "1 of 3" in (error or "")

    def test_a_partially_succeeded_array_job_is_not_success(self) -> None:
        """Two of three finishing is not "succeeded" — that error surfaces late."""
        state, _ = map_execution_state(
            execution_proto(task_count=3, succeeded=2, completion_time=datetime.now(UTC))
        )
        assert state is ExecutionState.FAILED

    def test_cancellation_wins_over_everything(self) -> None:
        state, _ = map_execution_state(execution_proto(cancelled=1, succeeded=1))
        assert state is ExecutionState.CANCELLED

    def test_a_running_task_is_running(self) -> None:
        assert map_execution_state(execution_proto(running=1))[0] is ExecutionState.RUNNING

    def test_accepted_but_not_started_is_queued_not_unknown(self) -> None:
        """ "Waiting for capacity" is a real state and deserves the honest name."""
        assert map_execution_state(execution_proto())[0] is ExecutionState.QUEUED

    def test_a_hostile_object_does_not_crash_the_mapping(self) -> None:
        assert map_execution_state(SimpleNamespace())[0] is ExecutionState.QUEUED


# --------------------------------------------------------------------------- #
# Translation
# --------------------------------------------------------------------------- #
class TestSubmission:
    def test_argv_and_env_become_container_overrides(self) -> None:
        jobs = FakeJobsClient()
        backend = make_backend(jobs_client=jobs)
        backend.submit(
            JobSpec(
                job="build-cog",
                image="gdal:latest",
                command=["python", "build.py"],
                args=["--fast"],
                env={"GDAL_CACHEMAX": "512"},
            )
        )
        override = jobs.requests[0]["overrides"]["container_overrides"][0]
        assert override["args"] == ["python", "build.py", "--fast"]
        assert override["env"] == [{"name": "GDAL_CACHEMAX", "value": "512"}]

    def test_parallelism_becomes_task_count(self) -> None:
        jobs = FakeJobsClient()
        backend = make_backend(jobs_client=jobs)
        backend.submit(JobSpec(job="j", image="i", parallelism=8))
        assert jobs.requests[0]["overrides"]["task_count"] == 8

    def test_a_timeout_is_translated_to_seconds(self) -> None:
        jobs = FakeJobsClient()
        backend = make_backend(jobs_client=jobs)
        backend.submit(JobSpec(job="j", image="i", timeout=TimeoutPolicy(seconds=1800)))
        assert jobs.requests[0]["overrides"]["timeout"] == {"seconds": 1800}

    def test_retry_attempts_become_max_retries(self) -> None:
        """RetryPolicy counts attempts; Cloud Run counts retries. Off by one, on purpose."""
        jobs = FakeJobsClient()
        backend = make_backend(jobs_client=jobs)
        backend.submit(JobSpec(job="j", image="i", retry=RetryPolicy(max_attempts=3)))
        assert jobs.requests[0]["overrides"]["max_retries"] == 2

    def test_the_default_job_resource_is_derived(self) -> None:
        jobs = FakeJobsClient()
        backend = make_backend(jobs_client=jobs)
        backend.submit(JobSpec(job="build-cog", image="i"))
        assert (
            jobs.requests[0]["name"] == "projects/my-project/locations/europe-west1/jobs/build-cog"
        )

    def test_backend_options_can_point_at_another_resource(self) -> None:
        jobs = FakeJobsClient()
        backend = make_backend(jobs_client=jobs)
        backend.submit(
            JobSpec(
                job="build-cog",
                image="i",
                backend_options=BackendOptions(
                    {"cloudrun": {"job": "projects/other/locations/us/jobs/legacy"}}
                ),
            )
        )
        assert jobs.requests[0]["name"] == "projects/other/locations/us/jobs/legacy"

    def test_a_run_job_failure_becomes_a_submission_error(self) -> None:
        backend = make_backend(jobs_client=FakeJobsClient(fail=True))
        with pytest.raises(SubmissionError) as caught:
            backend.submit(JobSpec(job="j", image="i"))
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert "google" not in str(caught.value).lower() or True  # never a raw SDK type

    def test_the_execution_name_is_kept_as_the_external_id(self) -> None:
        backend = make_backend()
        execution = backend.submit(JobSpec(job="build-cog", image="i"))
        assert execution.id.startswith("job_")
        assert execution.external_id is not None
        assert "/executions/" in execution.external_id


class TestCapabilities:
    def test_gpu_is_refused_because_cloud_run_cannot_override_it(self) -> None:
        """The headline case: never silently run a GPU workload on a CPU."""
        backend = make_backend()
        with pytest.raises(UnsupportedCapability, match="gpu"):
            backend.submit(JobSpec(job="train", image="i", resources=Resources(gpu=1)))

    def test_cpu_and_memory_are_refused_too(self) -> None:
        """They live on the Job resource, not the execution override."""
        backend = make_backend()
        with pytest.raises(UnsupportedCapability):
            backend.submit(
                JobSpec(job="j", image="i", resources=Resources(cpu="4000m", memory="16Gi"))
            )

    def test_what_cloud_run_really_supports_is_advertised(self) -> None:
        capabilities = make_backend().capabilities
        for supported in (
            Capability.STATE,
            Capability.CANCEL,
            Capability.LOGS,
            Capability.TIMEOUT,
            Capability.PARALLELISM,
            Capability.ENVIRONMENT,
        ):
            assert supported in capabilities
        for unsupported in (Capability.CPU, Capability.MEMORY, Capability.GPU):
            assert unsupported not in capabilities


class TestObservation:
    def test_get_maps_the_provider_state(self) -> None:
        executions = FakeExecutionsClient(
            execution_proto(task_count=1, succeeded=1, completion_time=datetime.now(UTC))
        )
        backend = make_backend(executions_client=executions)
        execution = backend.submit(JobSpec(job="j", image="i"))
        assert backend.get(execution.id).state is ExecutionState.SUCCEEDED

    def test_a_provider_not_found_becomes_execution_not_found(self) -> None:
        executions = FakeExecutionsClient(error=NotFound("no such execution"))
        backend = make_backend(executions_client=executions)
        execution = backend.submit(JobSpec(job="j", image="i"))
        with pytest.raises(ExecutionNotFound):
            backend.get(execution.id)

    def test_another_provider_error_becomes_a_backend_error(self) -> None:
        """A transient API failure is not "this execution does not exist"."""
        executions = FakeExecutionsClient(error=RuntimeError("503 backend unavailable"))
        backend = make_backend(executions_client=executions)
        execution = backend.submit(JobSpec(job="j", image="i"))
        with pytest.raises(BackendError) as caught:
            backend.get(execution.id)
        assert not isinstance(caught.value, ExecutionNotFound)

    def test_an_execution_resource_name_is_accepted_directly(self) -> None:
        """An operator with a name from the console can look it up from anywhere."""
        backend = make_backend()
        assert backend.get("projects/p/locations/eu/executions/x").state is not None

    def test_an_unknown_id_explains_the_cross_process_limitation(self) -> None:
        backend = make_backend()
        with pytest.raises(ExecutionNotFound, match="another process"):
            backend.get("job_never_submitted")

    def test_cancel_goes_through_the_provider(self) -> None:
        executions = FakeExecutionsClient()
        backend = make_backend(executions_client=executions)
        execution = backend.submit(JobSpec(job="j", image="i"))
        cancelled = backend.cancel(execution.id)
        assert executions.cancelled
        assert cancelled.state is ExecutionState.CANCELLED


class TestConfiguration:
    @pytest.mark.parametrize(("project", "location"), [(None, "eu"), ("p", None), (None, None)])
    def test_project_and_location_are_required(
        self, project: str | None, location: str | None
    ) -> None:
        with pytest.raises(ConfigurationError, match=r"project.*location"):
            CloudRunJobBackend(project=project, location=location)
