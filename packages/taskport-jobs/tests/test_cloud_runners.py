"""Tests for cloud job runners using injected SDK fakes (no cloud account needed)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from taskport.core import ProviderOptions, UnsupportedCapabilityError
from taskport.jobs import JobResources, JobSpec, JobStatus
from taskport.jobs.runners.aws import BatchRunner, map_batch_status
from taskport.jobs.runners.azure import ContainerAppsJobsRunner, map_aca_status
from taskport.jobs.runners.gcp import CloudRunJobsRunner, map_execution_status
from taskport.jobs.runners.kubernetes import KubernetesJobRunner, map_k8s_status


# --------------------------------------------------------------------------- #
# GCP Cloud Run Jobs
# --------------------------------------------------------------------------- #
class _FakeJobsClient:
    def __init__(self) -> None:
        self.last_request: dict | None = None

    def run_job(self, request: dict) -> SimpleNamespace:
        self.last_request = request
        return SimpleNamespace(metadata=SimpleNamespace(name="exec-42"))


class _FakeExecutionsClient:
    def __init__(self, execution: object) -> None:
        self._execution = execution
        self.cancelled: str | None = None

    def get_execution(self, name: str) -> object:
        return self._execution

    def cancel_execution(self, name: str) -> None:
        self.cancelled = name


def test_gcp_submit_builds_overrides_and_returns_execution_id() -> None:
    jobs = _FakeJobsClient()
    execs = _FakeExecutionsClient(
        SimpleNamespace(task_count=1, succeeded_count=1, completion_time="2026-07-24T00:00:00Z")
    )
    runner = CloudRunJobsRunner(
        project="p",
        location="us-central1",
        jobs_client=jobs,
        executions_client=execs,
    )
    handle = runner.run(
        JobSpec(name="etl", command=["--flag"], env={"K": "v"}, parallelism=3, timeout=120)
    )
    assert handle.provider_metadata.provider_id == "exec-42"
    assert jobs.last_request["name"].endswith("/jobs/etl")
    overrides = jobs.last_request["overrides"]
    assert overrides["container_overrides"][0]["args"] == ["--flag"]
    assert overrides["container_overrides"][0]["env"] == [{"name": "K", "value": "v"}]
    assert overrides["task_count"] == 3
    assert overrides["timeout"] == {"seconds": 120}

    result = runner.get(handle)
    assert result.status == JobStatus.SUCCEEDED
    runner.cancel(handle)
    assert execs.cancelled == "exec-42"


def test_gcp_respects_provider_options_job_resource() -> None:
    jobs = _FakeJobsClient()
    runner = CloudRunJobsRunner(
        project="p",
        location="l",
        jobs_client=jobs,
        executions_client=_FakeExecutionsClient(SimpleNamespace()),
    )
    spec = JobSpec(
        name="etl",
        provider_options=ProviderOptions({"gcp": {"job": "projects/x/locations/y/jobs/custom"}}),
    )
    runner.run(spec)
    assert jobs.last_request["name"] == "projects/x/locations/y/jobs/custom"


def test_gcp_rejects_cpu_override() -> None:
    runner = CloudRunJobsRunner(
        project="p",
        location="l",
        jobs_client=_FakeJobsClient(),
        executions_client=_FakeExecutionsClient(SimpleNamespace()),
    )
    with pytest.raises(UnsupportedCapabilityError):
        runner.run(JobSpec(name="x", resources=JobResources(cpu="2")))


@pytest.mark.parametrize(
    "execution,expected",
    [
        (SimpleNamespace(cancelled_count=1), JobStatus.CANCELLED),
        (
            SimpleNamespace(task_count=2, succeeded_count=2, completion_time="t"),
            JobStatus.SUCCEEDED,
        ),
        (SimpleNamespace(task_count=2, failed_count=1, completion_time="t"), JobStatus.FAILED),
        (SimpleNamespace(running_count=1), JobStatus.RUNNING),
        (SimpleNamespace(), JobStatus.RUNNING),
    ],
)
def test_gcp_status_mapping(execution: object, expected: JobStatus) -> None:
    status, _ = map_execution_status(execution)
    assert status == expected


# --------------------------------------------------------------------------- #
# AWS Batch
# --------------------------------------------------------------------------- #
class _FakeBatchClient:
    def __init__(self, describe: dict) -> None:
        self.last_submit: dict | None = None
        self.terminated: str | None = None
        self._describe = describe

    def submit_job(self, **kwargs: object) -> dict:
        self.last_submit = kwargs
        return {"jobId": "j-1"}

    def describe_jobs(self, jobs: list[str]) -> dict:
        return self._describe

    def terminate_job(self, jobId: str, reason: str) -> None:
        self.terminated = jobId


def test_aws_submit_and_lifecycle() -> None:
    client = _FakeBatchClient({"jobs": [{"status": "SUCCEEDED", "container": {"exitCode": 0}}]})
    runner = BatchRunner(region="us-east-1", job_queue="q", job_definition="d", client=client)
    handle = runner.run(
        JobSpec(
            name="etl",
            command=["run"],
            env={"K": "v"},
            parallelism=2,
            timeout=60,
            max_retries=1,
            resources=JobResources(cpu="1", memory="2048", gpu=1),
        )
    )
    assert handle.provider_metadata.provider_id == "j-1"
    submit = client.last_submit
    assert submit["jobQueue"] == "q"
    assert submit["arrayProperties"] == {"size": 2}
    assert submit["timeout"] == {"attemptDurationSeconds": 60}
    assert submit["retryStrategy"] == {"attempts": 2}
    assert {"type": "GPU", "value": "1"} in submit["containerOverrides"]["resourceRequirements"]

    result = runner.get(handle)
    assert result.status == JobStatus.SUCCEEDED
    runner.cancel(handle)
    assert client.terminated == "j-1"


def test_aws_requires_queue_and_definition() -> None:
    from taskport.core import ConfigurationError

    runner = BatchRunner(client=_FakeBatchClient({"jobs": []}))
    with pytest.raises(ConfigurationError):
        runner.run(JobSpec(name="etl", command=["run"]))


def test_aws_queue_via_provider_options() -> None:
    client = _FakeBatchClient({"jobs": []})
    runner = BatchRunner(client=client)
    spec = JobSpec(
        name="etl",
        command=["run"],
        provider_options=ProviderOptions({"aws": {"job_queue": "q2", "job_definition": "d2"}}),
    )
    runner.run(spec)
    assert client.last_submit["jobQueue"] == "q2"


@pytest.mark.parametrize(
    "job,expected",
    [
        ({"status": "SUCCEEDED"}, JobStatus.SUCCEEDED),
        ({"status": "FAILED", "statusReason": "oom"}, JobStatus.FAILED),
        ({"status": "RUNNING"}, JobStatus.RUNNING),
        ({"status": "RUNNABLE"}, JobStatus.PENDING),
        ({"status": "WEIRD"}, JobStatus.UNKNOWN),
    ],
)
def test_aws_status_mapping(job: dict, expected: JobStatus) -> None:
    assert map_batch_status(job)[0] == expected


# --------------------------------------------------------------------------- #
# Kubernetes
# --------------------------------------------------------------------------- #
class _FakeBatchApi:
    def __init__(self, status: object) -> None:
        self.created: dict | None = None
        self.deleted: str | None = None
        self._status = status

    def create_namespaced_job(self, namespace: str, body: dict) -> None:
        self.created = body

    def read_namespaced_job_status(self, name: str, namespace: str) -> object:
        return SimpleNamespace(status=self._status)

    def delete_namespaced_job(self, name: str, namespace: str, propagation_policy: str) -> None:
        self.deleted = name


def test_k8s_submit_builds_job_body_and_lifecycle() -> None:
    api = _FakeBatchApi(SimpleNamespace(succeeded=1))
    runner = KubernetesJobRunner(namespace="jobs", api=api, service_account="sa")
    handle = runner.run(
        JobSpec(
            name="reindex",
            image="repo/img:1",
            command=["run"],
            env={"K": "v"},
            parallelism=2,
            timeout=300,
            resources=JobResources(cpu="500m", memory="512Mi", gpu=2),
        )
    )
    body = api.created
    container = body["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "repo/img:1"
    assert container["resources"]["limits"]["nvidia.com/gpu"] == "2"
    assert body["spec"]["parallelism"] == 2
    assert body["spec"]["activeDeadlineSeconds"] == 300
    assert body["spec"]["template"]["spec"]["serviceAccountName"] == "sa"

    assert runner.get(handle).status == JobStatus.SUCCEEDED
    runner.cancel(handle)
    assert api.deleted == handle.provider_metadata.provider_id


def test_k8s_requires_image() -> None:
    from taskport.jobs.errors import JobError

    runner = KubernetesJobRunner(api=_FakeBatchApi(SimpleNamespace(active=1)))
    with pytest.raises(JobError):
        runner.run(JobSpec(name="x", command=["run"]))


@pytest.mark.parametrize(
    "status,expected",
    [
        (SimpleNamespace(succeeded=1), JobStatus.SUCCEEDED),
        (SimpleNamespace(failed=2), JobStatus.FAILED),
        (SimpleNamespace(active=1), JobStatus.RUNNING),
        (SimpleNamespace(), JobStatus.PENDING),
    ],
)
def test_k8s_status_mapping(status: object, expected: JobStatus) -> None:
    assert map_k8s_status(status)[0] == expected


# --------------------------------------------------------------------------- #
# Azure Container Apps Jobs
# --------------------------------------------------------------------------- #
class _FakePoller:
    def result(self) -> SimpleNamespace:
        return SimpleNamespace(name="exec-9")


class _FakeAzureJobs:
    def __init__(self) -> None:
        self.started: tuple | None = None
        self.stopped: tuple | None = None

    def begin_start(self, rg: str, name: str, template: dict) -> _FakePoller:
        self.started = (rg, name, template)
        return _FakePoller()

    def begin_stop_execution(self, rg: str, name: str, execution: str) -> None:
        self.stopped = (rg, name, execution)


class _FakeAzureClient:
    def __init__(self, executions: list[object]) -> None:
        self.jobs = _FakeAzureJobs()
        self.jobs_executions = SimpleNamespace(list=lambda rg, job: executions)


def test_azure_submit_and_lifecycle() -> None:
    client = _FakeAzureClient([SimpleNamespace(name="exec-9", status="Succeeded")])
    runner = ContainerAppsJobsRunner(subscription_id="s", resource_group="rg", client=client)
    handle = runner.run(
        JobSpec(
            name="job1",
            image="img",
            command=["run"],
            env={"K": "v"},
            resources=JobResources(cpu="0.5", memory="1Gi"),
        )
    )
    assert handle.provider_metadata.provider_id == "exec-9"
    assert client.jobs.started[0] == "rg"
    assert runner.get(handle).status == JobStatus.SUCCEEDED
    runner.cancel(handle)
    assert client.jobs.stopped == ("rg", "job1", "exec-9")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Succeeded", JobStatus.SUCCEEDED),
        ("Failed", JobStatus.FAILED),
        ("Running", JobStatus.RUNNING),
        ("Stopped", JobStatus.CANCELLED),
        (None, JobStatus.UNKNOWN),
        ("weird", JobStatus.UNKNOWN),
    ],
)
def test_azure_status_mapping(raw: str | None, expected: JobStatus) -> None:
    assert map_aca_status(raw) == expected
