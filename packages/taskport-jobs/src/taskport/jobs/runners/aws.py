"""BatchRunner — run a workload as an AWS Batch job (on Fargate or EC2).

Uses the default boto3 credential chain (ADR-0007). The boto3 client is imported
lazily and can be injected for testing. Job queue and job definition are supplied
via ``provider_options["aws"]`` or the runner's defaults.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from taskport.core import (
    CapabilitySet,
    ConfigurationError,
    ProviderError,
    ProviderMetadata,
    new_id,
)

from ..capabilities import JobCapability
from ..models import JobHandle, JobResult, JobSpec, JobStatus
from ..runner import BaseJobRunner

_AWS_CAPABILITIES = frozenset(
    {
        JobCapability.STATUS,
        JobCapability.CANCEL,
        JobCapability.LOGS,
        JobCapability.TIMEOUT,
        JobCapability.PARALLELISM,
        JobCapability.CPU_OVERRIDE,
        JobCapability.MEMORY_OVERRIDE,
        JobCapability.GPU,
        JobCapability.ENVIRONMENT_OVERRIDE,
    }
)

# AWS Batch status vocabulary -> portable status.
_RUNNING_STATES = {"SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING"}


def map_batch_status(job: dict[str, Any]) -> tuple[JobStatus, int | None, str | None]:
    """Map a Batch ``describe_jobs`` entry to (status, exit_code, error). Pure."""
    state = str(job.get("status", "")).upper()
    container = job.get("container", {}) or {}
    exit_code = container.get("exitCode")
    if state == "SUCCEEDED":
        return JobStatus.SUCCEEDED, exit_code, None
    if state == "FAILED":
        reason = job.get("statusReason") or container.get("reason")
        return JobStatus.FAILED, exit_code, reason
    if state in _RUNNING_STATES:
        return (
            JobStatus.RUNNING if state == "RUNNING" else JobStatus.PENDING,
            None,
            None,
        )
    return JobStatus.UNKNOWN, None, None


class BatchRunner(BaseJobRunner):
    """Runs AWS Batch jobs."""

    def __init__(
        self,
        *,
        region: str | None = None,
        job_queue: str | None = None,
        job_definition: str | None = None,
        client: Any = None,
    ) -> None:
        self._region = region
        self._job_queue = job_queue
        self._job_definition = job_definition
        self._client = client

    @property
    def provider(self) -> str:
        return "aws"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(_AWS_CAPABILITIES, provider="aws")

    def _batch(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover
                raise ProviderError(
                    "boto3 is required for BatchRunner; install taskport-jobs[aws]",
                    provider="aws",
                ) from exc
            self._client = boto3.client("batch", region_name=self._region)
        return self._client

    def _resolve(self, spec: JobSpec, key: str, default: str | None) -> str:
        value = spec.provider_options.for_provider("aws").get(key, default)
        if not isinstance(value, str) or not value:
            raise ConfigurationError(
                f"AWS Batch requires {key!r} (set on the runner or in provider_options['aws'])"
            )
        return value

    def _submit(self, spec: JobSpec) -> JobHandle:
        overrides: dict[str, Any] = {}
        if spec.command:
            overrides["command"] = list(spec.command)
        if spec.env:
            overrides["environment"] = [{"name": k, "value": v} for k, v in spec.env.items()]
        resource_reqs = self._resource_requirements(spec)
        if resource_reqs:
            overrides["resourceRequirements"] = resource_reqs

        request: dict[str, Any] = {
            "jobName": spec.name,
            "jobQueue": self._resolve(spec, "job_queue", self._job_queue),
            "jobDefinition": self._resolve(spec, "job_definition", self._job_definition),
        }
        if overrides:
            request["containerOverrides"] = overrides
        if spec.parallelism > 1:
            request["arrayProperties"] = {"size": spec.parallelism}
        if spec.timeout is not None:
            request["timeout"] = {"attemptDurationSeconds": int(spec.timeout)}
        if spec.max_retries > 0:
            request["retryStrategy"] = {"attempts": spec.max_retries + 1}

        try:
            response = self._batch().submit_job(**request)
        except Exception as exc:
            raise ProviderError(f"submit_job failed: {exc}", provider="aws") from exc

        job_id = response.get("jobId")
        metadata = ProviderMetadata(
            provider="aws",
            provider_id=job_id,
            region=self._region,
            resource=request["jobDefinition"],
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
        job_id = handle.provider_metadata.provider_id
        if not job_id:
            return JobResult(handle=handle, status=JobStatus.UNKNOWN)
        try:
            response = self._batch().describe_jobs(jobs=[job_id])
        except Exception as exc:
            raise ProviderError(f"describe_jobs failed: {exc}", provider="aws") from exc
        jobs = response.get("jobs", [])
        if not jobs:
            return JobResult(handle=handle, status=JobStatus.UNKNOWN)
        status, exit_code, error = map_batch_status(jobs[0])
        return JobResult(handle=handle, status=status, exit_code=exit_code, error=error)

    def _cancel(self, handle: JobHandle) -> None:
        job_id = handle.provider_metadata.provider_id
        if not job_id:
            return
        try:
            self._batch().terminate_job(jobId=job_id, reason="cancelled via taskport")
        except Exception as exc:
            raise ProviderError(f"terminate_job failed: {exc}", provider="aws") from exc

    @staticmethod
    def _resource_requirements(spec: JobSpec) -> list[dict[str, str]]:
        reqs: list[dict[str, str]] = []
        if spec.resources.cpu is not None:
            reqs.append({"type": "VCPU", "value": spec.resources.cpu})
        if spec.resources.memory is not None:
            reqs.append({"type": "MEMORY", "value": spec.resources.memory})
        if spec.resources.gpu > 0:
            reqs.append({"type": "GPU", "value": str(spec.resources.gpu)})
        return reqs


def make_batch_runner(**kwargs: object) -> BatchRunner:
    return BatchRunner(
        region=kwargs.get("region"),  # type: ignore[arg-type]
        job_queue=kwargs.get("job_queue"),  # type: ignore[arg-type]
        job_definition=kwargs.get("job_definition"),  # type: ignore[arg-type]
    )


__all__ = ["BatchRunner", "make_batch_runner", "map_batch_status"]
