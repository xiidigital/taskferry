"""CloudRunJobsRunner — run a Cloud Run Job execution (GCP).

Cloud Run Jobs run an *existing* Job resource to completion, optionally with
per-execution container overrides (args/env). This is a scale-to-zero,
run-to-completion model with no standing worker (section 44).

Design notes:

* The Google SDK is imported **lazily** inside ``_client`` so
  ``import taskport.jobs`` never pulls in ``google.cloud`` (section 31).
* Clients can be **injected** for testing (ADR-0007): pass ``jobs_client`` /
  ``executions_client`` fakes; no GCP account is needed for the test suite.
* Status mapping is a **pure function** (:func:`map_execution_status`) so it is
  trivially unit-tested against duck-typed execution objects.
* Capabilities are advertised **conservatively and honestly** (ADR-0005): Cloud
  Run execution overrides cover args/env/task-count/timeout, so CPU/memory/GPU
  overrides are *not* claimed — a spec requesting them is rejected rather than
  silently ignored.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from taskport.core import (
    CapabilitySet,
    ProviderError,
    ProviderMetadata,
    new_id,
)

from ..capabilities import JobCapability
from ..models import JobHandle, JobResult, JobSpec, JobStatus
from ..runner import BaseJobRunner

_GCP_CAPABILITIES = frozenset(
    {
        JobCapability.STATUS,
        JobCapability.CANCEL,
        JobCapability.LOGS,
        JobCapability.TIMEOUT,
        JobCapability.PARALLELISM,
        JobCapability.ENVIRONMENT_OVERRIDE,
    }
)


def map_execution_status(execution: Any) -> tuple[JobStatus, str | None]:
    """Map a Cloud Run ``Execution`` to a portable status. Pure and defensive.

    Reads counts/completion defensively so it works with both real protos and
    simple fakes in tests.
    """
    task_count = int(getattr(execution, "task_count", 0) or 0)
    succeeded = int(getattr(execution, "succeeded_count", 0) or 0)
    failed = int(getattr(execution, "failed_count", 0) or 0)
    running = int(getattr(execution, "running_count", 0) or 0)
    cancelled = int(getattr(execution, "cancelled_count", 0) or 0)
    completion_time = getattr(execution, "completion_time", None)

    if cancelled > 0:
        return JobStatus.CANCELLED, None
    if completion_time:
        if failed > 0:
            return JobStatus.FAILED, f"{failed} task(s) failed"
        if task_count > 0 and succeeded >= task_count:
            return JobStatus.SUCCEEDED, None
        return JobStatus.FAILED, "completed without success"
    if running > 0 or not completion_time:
        return JobStatus.RUNNING, None
    return JobStatus.UNKNOWN, None


class CloudRunJobsRunner(BaseJobRunner):
    """Runs Cloud Run Job executions."""

    def __init__(
        self,
        *,
        project: str,
        location: str,
        jobs_client: Any = None,
        executions_client: Any = None,
    ) -> None:
        self._project = project
        self._location = location
        self._jobs_client = jobs_client
        self._executions_client = executions_client

    @property
    def provider(self) -> str:
        return "gcp"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(_GCP_CAPABILITIES, provider="gcp")

    # -- clients (lazy) ----------------------------------------------------- #
    def _jobs(self) -> Any:
        if self._jobs_client is None:
            try:
                from google.cloud import run_v2
            except ImportError as exc:  # pragma: no cover - env-specific
                raise ProviderError(
                    "google-cloud-run is required for CloudRunJobsRunner; "
                    "install taskport-jobs[gcp]",
                    provider="gcp",
                ) from exc
            self._jobs_client = run_v2.JobsClient()
        return self._jobs_client

    def _executions(self) -> Any:
        if self._executions_client is None:
            from google.cloud import run_v2

            self._executions_client = run_v2.ExecutionsClient()
        return self._executions_client

    # -- resource names ----------------------------------------------------- #
    def _job_resource(self, spec: JobSpec) -> str:
        override = spec.provider_options.for_provider("gcp").get("job")
        if isinstance(override, str):
            return override
        return f"projects/{self._project}/locations/{self._location}/jobs/{spec.name}"

    # -- template methods --------------------------------------------------- #
    def _submit(self, spec: JobSpec) -> JobHandle:
        container_override: dict[str, Any] = {}
        if spec.command:
            container_override["args"] = list(spec.command)
        if spec.env:
            container_override["env"] = [{"name": k, "value": v} for k, v in spec.env.items()]
        overrides: dict[str, Any] = {}
        if container_override:
            overrides["container_overrides"] = [container_override]
        if spec.parallelism > 1:
            overrides["task_count"] = spec.parallelism
        if spec.timeout is not None:
            overrides["timeout"] = {"seconds": int(spec.timeout)}

        request: dict[str, Any] = {"name": self._job_resource(spec)}
        if overrides:
            request["overrides"] = overrides

        try:
            operation = self._jobs().run_job(request=request)
        except Exception as exc:
            raise ProviderError(f"run_job failed: {exc}", provider="gcp") from exc

        execution_name = self._execution_name(operation)
        metadata = ProviderMetadata(
            provider="gcp",
            provider_id=execution_name,
            region=self._location,
            resource=self._job_resource(spec),
            labels=dict(spec.labels),
        )
        return JobHandle(
            id=new_id("job"),
            name=spec.name,
            provider_metadata=metadata,
            created_at=datetime.now(UTC),
            correlation=spec.correlation,
        )

    def _poll(self, handle: JobHandle) -> JobResult:
        execution_name = handle.provider_metadata.provider_id
        if not execution_name:
            return JobResult(handle=handle, status=JobStatus.UNKNOWN)
        try:
            execution = self._executions().get_execution(name=execution_name)
        except Exception as exc:
            raise ProviderError(f"get_execution failed: {exc}", provider="gcp") from exc
        status, error = map_execution_status(execution)
        return JobResult(
            handle=handle,
            status=status,
            error=error,
            logs_uri=self._logs_uri(execution_name),
        )

    def _cancel(self, handle: JobHandle) -> None:
        execution_name = handle.provider_metadata.provider_id
        if not execution_name:
            return
        try:
            self._executions().cancel_execution(name=execution_name)
        except Exception as exc:
            raise ProviderError(f"cancel_execution failed: {exc}", provider="gcp") from exc

    # -- helpers ------------------------------------------------------------ #
    @staticmethod
    def _execution_name(operation: Any) -> str | None:
        metadata = getattr(operation, "metadata", None)
        name = getattr(metadata, "name", None)
        return name if isinstance(name, str) else getattr(operation, "name", None)

    def _logs_uri(self, execution_name: str) -> str:
        # Best-effort Cloud Logging console link (no request performed).
        return f"https://console.cloud.google.com/logs/query?project={self._project}"


def make_cloud_run_jobs_runner(**kwargs: object) -> CloudRunJobsRunner:
    """Factory for ``runners.register_spec`` / ``configure``."""
    return CloudRunJobsRunner(
        project=str(kwargs["project"]),
        location=str(kwargs["location"]),
    )


__all__ = ["CloudRunJobsRunner", "make_cloud_run_jobs_runner", "map_execution_status"]
