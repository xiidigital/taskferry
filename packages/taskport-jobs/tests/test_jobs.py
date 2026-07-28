"""AWS Batch, Kubernetes and Azure Container Apps job backends.

The theme: three runtimes with genuinely different abilities, one `JobSpec`, and
capability sets that tell the truth about the differences instead of flattening
them.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from taskport import (
    BackendOptions,
    Capability,
    ExecutionState,
    JobSpec,
    Resources,
    TimeoutPolicy,
)
from taskport.contract import JobBackendContract
from taskport.errors import (
    ConfigurationError,
    ExecutionNotFound,
    SubmissionError,
    UnsupportedCapability,
)
from taskport.ports import ExecutionBackend
from taskport.retry import RetryPolicy
from taskport_jobs import (
    BatchJobBackend,
    ContainerAppsJobBackend,
    KubernetesJobBackend,
    map_batch_state,
    map_execution_state,
    map_job_state,
)


# --------------------------------------------------------------------------- #
# AWS Batch
# --------------------------------------------------------------------------- #
class FakeBatchClient:
    def __init__(self, job: dict[str, Any] | None = None, *, fail: bool = False) -> None:
        self.submitted: list[dict[str, Any]] = []
        self.terminated: list[str] = []
        self.job = job if job is not None else {"status": "RUNNABLE", "jobName": "j"}
        self.fail = fail

    def submit_job(self, **request: Any) -> dict[str, str]:
        if self.fail:
            raise RuntimeError("AccessDeniedException")
        self.submitted.append(request)
        return {"jobId": f"batch-{len(self.submitted)}"}

    def describe_jobs(self, *, jobs: list[str]) -> dict[str, list[dict[str, Any]]]:
        return {"jobs": [self.job] if self.job else []}

    def terminate_job(self, *, jobId: str, reason: str) -> None:
        self.terminated.append(jobId)
        self.job = {"status": "FAILED", "statusReason": "cancelled", "jobName": "j"}


def batch_backend(**overrides: Any) -> BatchJobBackend:
    return BatchJobBackend(
        job_queue=overrides.pop("job_queue", "default-queue"),
        job_definition=overrides.pop("job_definition", "default-def"),
        client=overrides.pop("client", FakeBatchClient()),
        **overrides,
    )


class TestBatchContract(JobBackendContract):
    def make_backend(self) -> ExecutionBackend:
        return batch_backend()

    def success_spec(self) -> JobSpec:
        return JobSpec(job="etl", image="etl:latest")


class TestBatchTranslation:
    def test_resources_become_resource_requirements(self) -> None:
        client = FakeBatchClient()
        backend = batch_backend(client=client)
        backend.submit(JobSpec(job="train", resources=Resources(cpu="4000m", memory="16Gi", gpu=2)))
        requirements = client.submitted[0]["containerOverrides"]["resourceRequirements"]
        assert {"type": "VCPU", "value": "4"} in requirements
        assert {"type": "MEMORY", "value": "16384"} in requirements
        assert {"type": "GPU", "value": "2"} in requirements

    def test_fractional_cpu_survives_the_conversion(self) -> None:
        client = FakeBatchClient()
        batch_backend(client=client).submit(JobSpec(job="j", resources=Resources(cpu="500m")))
        vcpu = client.submitted[0]["containerOverrides"]["resourceRequirements"][0]
        assert vcpu == {"type": "VCPU", "value": "0.5"}

    def test_parallelism_becomes_array_properties(self) -> None:
        client = FakeBatchClient()
        batch_backend(client=client).submit(JobSpec(job="j", parallelism=10))
        assert client.submitted[0]["arrayProperties"] == {"size": 10}

    def test_a_timeout_becomes_attempt_duration(self) -> None:
        client = FakeBatchClient()
        batch_backend(client=client).submit(JobSpec(job="j", timeout=TimeoutPolicy(seconds=600)))
        assert client.submitted[0]["timeout"] == {"attemptDurationSeconds": 600}

    def test_retry_attempts_pass_straight_through(self) -> None:
        """Batch counts total attempts, exactly like RetryPolicy — no off-by-one."""
        client = FakeBatchClient()
        batch_backend(client=client).submit(JobSpec(job="j", retry=RetryPolicy(max_attempts=3)))
        assert client.submitted[0]["retryStrategy"] == {"attempts": 3}

    def test_the_queue_can_be_overridden_per_spec(self) -> None:
        client = FakeBatchClient()
        batch_backend(client=client).submit(
            JobSpec(job="j", backend_options=BackendOptions({"aws": {"job_queue": "gpu-queue"}}))
        )
        assert client.submitted[0]["jobQueue"] == "gpu-queue"

    def test_a_missing_job_definition_is_a_configuration_error(self) -> None:
        backend = BatchJobBackend(job_queue="q", client=FakeBatchClient())
        with pytest.raises(ConfigurationError, match="job_definition"):
            backend.submit(JobSpec(job="j"))

    def test_an_unsafe_job_name_is_sanitised(self) -> None:
        client = FakeBatchClient()
        batch_backend(client=client).submit(JobSpec(job="build/cog v2"))
        assert client.submitted[0]["jobName"] == "build-cog-v2"

    def test_a_submit_failure_becomes_a_submission_error(self) -> None:
        with pytest.raises(SubmissionError):
            batch_backend(client=FakeBatchClient(fail=True)).submit(JobSpec(job="j"))


class TestBatchStateMapping:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            ("SUBMITTED", ExecutionState.QUEUED),
            ("PENDING", ExecutionState.QUEUED),
            ("RUNNABLE", ExecutionState.QUEUED),
            ("STARTING", ExecutionState.RUNNING),
            ("RUNNING", ExecutionState.RUNNING),
            ("SUCCEEDED", ExecutionState.SUCCEEDED),
        ],
    )
    def test_known_statuses_map(self, status: str, expected: ExecutionState) -> None:
        state, _, _ = map_batch_state({"status": status})
        assert state is expected

    def test_a_timeout_failure_is_reported_as_timed_out(self) -> None:
        """More useful to an operator than a generic failure."""
        state, _, error = map_batch_state(
            {"status": "FAILED", "statusReason": "Job attempt duration exceeded timeout"}
        )
        assert state is ExecutionState.TIMED_OUT
        assert "timeout" in (error or "").lower()

    def test_an_ordinary_failure_carries_the_reason_and_exit_code(self) -> None:
        state, exit_code, error = map_batch_state(
            {"status": "FAILED", "statusReason": "OutOfMemoryError", "container": {"exitCode": 137}}
        )
        assert state is ExecutionState.FAILED
        assert exit_code == 137
        assert error == "OutOfMemoryError"

    def test_an_unknown_status_does_not_become_a_guess(self) -> None:
        assert map_batch_state({"status": "SOMETHING_NEW"})[0] is ExecutionState.UNKNOWN

    def test_get_maps_provider_state_and_timestamps(self) -> None:
        client = FakeBatchClient(
            {
                "status": "SUCCEEDED",
                "jobName": "etl",
                "startedAt": 1_700_000_000_000,
                "stoppedAt": 1_700_000_060_000,
                "container": {"exitCode": 0},
            }
        )
        backend = batch_backend(client=client)
        execution = backend.submit(JobSpec(job="etl"))
        fetched = backend.get(execution.id)
        assert fetched.state is ExecutionState.SUCCEEDED
        assert fetched.started_at is not None
        assert fetched.finished_at is not None

    def test_a_vanished_job_raises_execution_not_found(self) -> None:
        client = FakeBatchClient()
        backend = batch_backend(client=client)
        execution = backend.submit(JobSpec(job="j"))
        client.job = {}
        with pytest.raises(ExecutionNotFound):
            backend.get(execution.id)


class TestBatchCapabilities:
    def test_batch_is_the_backend_that_supports_everything(self) -> None:
        """Per-submission overrides really do cover CPU, memory and GPU here."""
        capabilities = batch_backend().capabilities
        for capability in (Capability.CPU, Capability.MEMORY, Capability.GPU):
            assert capability in capabilities

    def test_a_gpu_spec_that_cloud_run_refuses_runs_here(self) -> None:
        """The same spec, two backends, two honest and different answers."""
        client = FakeBatchClient()
        execution = batch_backend(client=client).submit(
            JobSpec(job="train", resources=Resources(gpu=4, gpu_type="a100"))
        )
        assert execution.state is ExecutionState.QUEUED


# --------------------------------------------------------------------------- #
# Kubernetes
# --------------------------------------------------------------------------- #
class ApiException(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


class FakeBatchV1Api:
    def __init__(self, status: Any = None, *, missing: bool = False) -> None:
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.status = status if status is not None else SimpleNamespace(active=1)
        self.missing = missing

    def create_namespaced_job(self, *, namespace: str, body: dict[str, Any]) -> None:
        self.created.append(body)

    def read_namespaced_job_status(self, *, name: str, namespace: str) -> Any:
        if self.missing:
            raise ApiException(404)
        return SimpleNamespace(status=self.status, spec=SimpleNamespace(completions=1))

    def delete_namespaced_job(self, *, name: str, namespace: str, propagation_policy: str) -> None:
        self.deleted.append(name)


def k8s_backend(**overrides: Any) -> KubernetesJobBackend:
    return KubernetesJobBackend(api=overrides.pop("api", FakeBatchV1Api()), **overrides)


class TestKubernetesContract(JobBackendContract):
    def make_backend(self) -> ExecutionBackend:
        return k8s_backend()

    def success_spec(self) -> JobSpec:
        return JobSpec(job="etl", image="etl:latest")


class TestKubernetesManifest:
    def test_resources_need_no_translation_at_all(self) -> None:
        """Resources uses Kubernetes quantity syntax precisely so this is lossless."""
        backend = k8s_backend()
        spec = JobSpec(
            job="train", image="i", resources=Resources(cpu="1000m", memory="512Mi", gpu=1)
        )
        manifest = backend.build_manifest(spec, "train-abc")
        limits = manifest["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]
        assert limits == {"cpu": "1000m", "memory": "512Mi", "nvidia.com/gpu": "1"}

    def test_requests_equal_limits(self) -> None:
        """A batch job throttled halfway through is worse than one that waits."""
        manifest = k8s_backend().build_manifest(
            JobSpec(job="j", image="i", resources=Resources(cpu="2")), "j-1"
        )
        resources = manifest["spec"]["template"]["spec"]["containers"][0]["resources"]
        assert resources["requests"] == resources["limits"]

    def test_command_and_args_stay_separate(self) -> None:
        manifest = k8s_backend().build_manifest(
            JobSpec(job="j", image="i", command=["python"], args=["etl.py"]), "j-1"
        )
        container = manifest["spec"]["template"]["spec"]["containers"][0]
        assert container["command"] == ["python"]
        assert container["args"] == ["etl.py"]

    def test_retry_attempts_become_a_backoff_limit(self) -> None:
        """backoffLimit counts retries; RetryPolicy counts attempts."""
        manifest = k8s_backend().build_manifest(
            JobSpec(job="j", image="i", retry=RetryPolicy(max_attempts=4)), "j-1"
        )
        assert manifest["spec"]["backoffLimit"] == 3

    def test_a_timeout_becomes_an_active_deadline(self) -> None:
        manifest = k8s_backend().build_manifest(
            JobSpec(job="j", image="i", timeout=TimeoutPolicy(seconds=900)), "j-1"
        )
        assert manifest["spec"]["activeDeadlineSeconds"] == 900

    def test_parallelism_sets_both_parallelism_and_completions(self) -> None:
        manifest = k8s_backend().build_manifest(JobSpec(job="j", image="i", parallelism=5), "j-1")
        assert manifest["spec"]["parallelism"] == 5
        assert manifest["spec"]["completions"] == 5

    def test_node_selectors_come_from_backend_options(self) -> None:
        """A Kubernetes-only concept, kept out of JobSpec and available anyway."""
        manifest = k8s_backend().build_manifest(
            JobSpec(
                job="j",
                image="i",
                backend_options=BackendOptions(
                    {"kubernetes": {"node_selector": {"cloud.google.com/gke-accelerator": "a100"}}}
                ),
            ),
            "j-1",
        )
        selector = manifest["spec"]["template"]["spec"]["nodeSelector"]
        assert selector["cloud.google.com/gke-accelerator"] == "a100"

    def test_a_job_without_an_image_is_refused(self) -> None:
        with pytest.raises(SubmissionError, match="no image"):
            k8s_backend().build_manifest(JobSpec(job="j"), "j-1")

    def test_the_generated_name_is_dns_1123(self) -> None:
        api = FakeBatchV1Api()
        k8s_backend(api=api).submit(JobSpec(job="Build_COG v2", image="i"))
        name = api.created[0]["metadata"]["name"]
        assert name.replace("-", "").isalnum()
        assert name == name.lower()

    def test_finished_jobs_are_cleaned_up_by_kubernetes(self) -> None:
        manifest = k8s_backend().build_manifest(JobSpec(job="j", image="i"), "j-1")
        assert manifest["spec"]["ttlSecondsAfterFinished"] == 3600


class TestKubernetesStateMapping:
    def test_all_pods_succeeded(self) -> None:
        state, _ = map_job_state(SimpleNamespace(succeeded=1, failed=0, active=0))
        assert state is ExecutionState.SUCCEEDED

    def test_a_partially_complete_array_job_is_still_running(self) -> None:
        """One of four succeeding is not success — reporting it as such loses data."""
        status = SimpleNamespace(succeeded=1, failed=0, active=3)
        state, _ = map_job_state(status, completions=4)
        assert state is ExecutionState.RUNNING

    def test_a_deadline_failure_is_reported_as_timed_out(self) -> None:
        status = SimpleNamespace(
            succeeded=0,
            failed=1,
            active=0,
            conditions=[SimpleNamespace(type="Failed", reason="DeadlineExceeded")],
        )
        state, error = map_job_state(status)
        assert state is ExecutionState.TIMED_OUT
        assert error == "DeadlineExceeded"

    def test_nothing_active_yet_is_queued(self) -> None:
        state, _ = map_job_state(SimpleNamespace(succeeded=0, failed=0, active=0))
        assert state is ExecutionState.QUEUED

    def test_a_missing_job_raises_execution_not_found(self) -> None:
        api = FakeBatchV1Api()
        backend = k8s_backend(api=api)
        execution = backend.submit(JobSpec(job="j", image="i"))
        api.missing = True
        with pytest.raises(ExecutionNotFound):
            backend.get(execution.id)

    def test_cancel_deletes_the_job_and_its_pods(self) -> None:
        """Without background propagation the Job goes and the pods keep running."""
        api = FakeBatchV1Api()
        backend = k8s_backend(api=api)
        execution = backend.submit(JobSpec(job="j", image="i"))
        cancelled = backend.cancel(execution.id)
        assert api.deleted
        assert cancelled.state is ExecutionState.CANCELLED


# --------------------------------------------------------------------------- #
# Azure Container Apps
# --------------------------------------------------------------------------- #
class FakeJobsOperations:
    def __init__(self, sink: list[Any]) -> None:
        self.sink = sink
        self.stopped: list[str] = []

    def begin_start(self, group: str, name: str, template: dict[str, Any]) -> Any:
        self.sink.append((group, name, template))
        return SimpleNamespace(result=lambda: SimpleNamespace(name=f"exec-{len(self.sink)}"))

    def begin_stop_execution(self, group: str, job: str, execution: str) -> None:
        self.stopped.append(execution)


class FakeAcaClient:
    def __init__(self, status: str = "Running") -> None:
        self.started: list[Any] = []
        self.jobs = FakeJobsOperations(self.started)
        self.status = status

    @property
    def jobs_executions(self) -> Any:
        outer = self

        class Executions:
            def list(self, group: str, job: str) -> list[Any]:
                return [
                    SimpleNamespace(name=f"exec-{i + 1}", status=outer.status)
                    for i in range(len(outer.started))
                ]

        return Executions()


def aca_backend(**overrides: Any) -> ContainerAppsJobBackend:
    return ContainerAppsJobBackend(
        subscription_id=overrides.pop("subscription_id", "sub-1"),
        resource_group=overrides.pop("resource_group", "rg-1"),
        client=overrides.pop("client", FakeAcaClient()),
        **overrides,
    )


class TestContainerAppsContract(JobBackendContract):
    def make_backend(self) -> ExecutionBackend:
        return aca_backend()

    def success_spec(self) -> JobSpec:
        return JobSpec(job="etl", image="etl:latest")


class TestContainerApps:
    def test_cpu_is_converted_to_fractional_cores(self) -> None:
        client = FakeAcaClient()
        aca_backend(client=client).submit(
            JobSpec(job="j", image="i", resources=Resources(cpu="500m"))
        )
        container = client.started[0][2]["containers"][0]
        assert container["resources"]["cpu"] == 0.5

    def test_memory_is_converted_to_gibibytes(self) -> None:
        client = FakeAcaClient()
        aca_backend(client=client).submit(
            JobSpec(job="j", image="i", resources=Resources(memory="512Mi"))
        )
        assert client.started[0][2]["containers"][0]["resources"]["memory"] == "0.5Gi"

    def test_gpu_is_refused_because_azure_cannot_override_it(self) -> None:
        with pytest.raises(UnsupportedCapability, match="gpu"):
            aca_backend().submit(JobSpec(job="j", image="i", resources=Resources(gpu=1)))

    def test_cpu_and_memory_are_supported_unlike_cloud_run(self) -> None:
        """Two adapters, same shape, different honest answers."""
        capabilities = aca_backend().capabilities
        assert Capability.CPU in capabilities
        assert Capability.MEMORY in capabilities
        assert Capability.GPU not in capabilities

    def test_a_timeout_becomes_a_replica_timeout(self) -> None:
        client = FakeAcaClient()
        aca_backend(client=client).submit(
            JobSpec(job="j", image="i", timeout=TimeoutPolicy(seconds=1200))
        )
        assert client.started[0][2]["replicaTimeout"] == 1200

    @pytest.mark.parametrize(
        ("azure_status", "expected"),
        [
            ("Succeeded", ExecutionState.SUCCEEDED),
            ("Failed", ExecutionState.FAILED),
            ("Stopped", ExecutionState.CANCELLED),
            ("Running", ExecutionState.RUNNING),
            ("Processing", ExecutionState.RUNNING),
        ],
    )
    def test_status_mapping(self, azure_status: str, expected: ExecutionState) -> None:
        assert map_execution_state(azure_status) is expected

    def test_an_unknown_status_does_not_become_success(self) -> None:
        assert map_execution_state("SomeNewAzureStatus") is ExecutionState.UNKNOWN
        assert map_execution_state(None) is ExecutionState.UNKNOWN

    def test_get_reads_the_execution_status(self) -> None:
        client = FakeAcaClient(status="Succeeded")
        backend = aca_backend(client=client)
        execution = backend.submit(JobSpec(job="j", image="i"))
        assert backend.get(execution.id).state is ExecutionState.SUCCEEDED

    def test_missing_configuration_is_reported_at_construction(self) -> None:
        with pytest.raises(ConfigurationError, match=r"subscription_id.*resource_group"):
            ContainerAppsJobBackend()

    def test_cancel_stops_the_execution(self) -> None:
        client = FakeAcaClient(status="Stopped")
        backend = aca_backend(client=client)
        execution = backend.submit(JobSpec(job="j", image="i"))
        cancelled = backend.cancel(execution.id)
        assert client.jobs.stopped
        assert cancelled.state is ExecutionState.CANCELLED


# --------------------------------------------------------------------------- #
# The capability matrix, asserted rather than merely documented
# --------------------------------------------------------------------------- #
class TestCapabilityMatrix:
    def test_the_documented_differences_are_real(self) -> None:
        """The README's table is a claim; this is the check that keeps it true."""
        from taskport.backends.subprocess import SubprocessJobBackend

        matrix = {
            "batch": batch_backend().capabilities,
            "kubernetes": k8s_backend().capabilities,
            "containerapps": aca_backend().capabilities,
            "subprocess": SubprocessJobBackend().capabilities,
        }

        gpu_capable = {name for name, caps in matrix.items() if Capability.GPU in caps}
        assert gpu_capable == {"batch", "kubernetes"}

        cpu_capable = {name for name, caps in matrix.items() if Capability.CPU in caps}
        assert cpu_capable == {"batch", "kubernetes", "containerapps"}

        # Every job backend can at least submit, be observed and be cancelled.
        for name, caps in matrix.items():
            assert Capability.SUBMIT in caps, name
            assert Capability.STATE in caps, name
            assert Capability.CANCEL in caps, name

    def test_no_backend_pretends_to_do_something_it_cannot(self) -> None:
        """For every capability a backend omits, the matching spec must be refused."""
        probes = {
            Capability.GPU: JobSpec(job="p", image="i", resources=Resources(gpu=1)),
            Capability.PARALLELISM: JobSpec(job="p", image="i", parallelism=4),
            Capability.TIMEOUT: JobSpec(job="p", image="i", timeout=TimeoutPolicy(seconds=60)),
        }
        for backend in (batch_backend(), k8s_backend(), aca_backend()):
            for capability, spec in probes.items():
                if capability in backend.capabilities:
                    continue
                with pytest.raises(UnsupportedCapability):
                    backend.submit(spec)
