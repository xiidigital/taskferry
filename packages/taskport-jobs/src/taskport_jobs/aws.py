"""AWS Batch `JobBackend`.

```mermaid
flowchart LR
    SPEC["JobSpec"]
    B["BatchJobBackend"]
    API["batch:SubmitJob"]
    Q["job queue"]
    C["Fargate / EC2 container"]

    SPEC --> B --> API --> Q --> C
```

Batch is the most capable of the job backends: per-submission overrides really do
cover vCPU, memory, GPU, environment, array size, timeout and retry attempts. So
this is the one backend that advertises the full resource capability set, and a
``Resources(gpu=2)`` spec that Cloud Run must reject runs here as asked. Routing
GPU work at ``profile="gpu"`` and pointing that profile here is the whole
mechanism — no application code changes.

``boto3`` is imported lazily and the client is injectable, so the tests need no
AWS account and ``import taskport_jobs`` costs nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from taskport.capabilities import Capability, CapabilitySet
from taskport.core.provider import ProviderMetadata
from taskport.errors import BackendError, ConfigurationError, ExecutionNotFound, SubmissionError
from taskport.execution import (
    Execution,
    ExecutionId,
    ExecutionKind,
    ExecutionResult,
    ExecutionState,
    new_execution_id,
)
from taskport.ports import BaseBackend
from taskport.specs import ExecutionSpec, JobSpec
from taskport.tracking import ExternalIdIndex

BATCH_CAPABILITIES = frozenset(
    {
        Capability.SUBMIT,
        Capability.STATE,
        Capability.CANCEL,
        Capability.LOGS,
        Capability.RESULT,
        Capability.TIMEOUT,
        Capability.PARALLELISM,
        Capability.RETRY,
        Capability.CPU,
        Capability.MEMORY,
        Capability.GPU,
        Capability.ENVIRONMENT,
    }
)

#: Batch statuses that mean "accepted, not finished". SUBMITTED/PENDING/RUNNABLE
#: are genuinely queued; STARTING/RUNNING are genuinely running. Keeping the
#: distinction lets an operator tell "waiting for capacity" from "working".
_QUEUED = frozenset({"SUBMITTED", "PENDING", "RUNNABLE"})
_RUNNING = frozenset({"STARTING", "RUNNING"})


def map_batch_state(job: dict[str, Any]) -> tuple[ExecutionState, int | None, str | None]:
    """Map a ``describe_jobs`` entry to (state, exit code, error). Pure."""
    status = str(job.get("status", "")).upper()
    container = job.get("container") or {}
    exit_code = container.get("exitCode")
    if status == "SUCCEEDED":
        return ExecutionState.SUCCEEDED, exit_code, None
    if status == "FAILED":
        reason = str(job.get("statusReason") or container.get("reason") or "job failed")
        # Batch reports a timeout as a failure with a distinctive reason. Saying
        # TIMED_OUT is more useful to whoever has to act on the state.
        if "timeout" in reason.lower():
            return ExecutionState.TIMED_OUT, exit_code, reason
        return ExecutionState.FAILED, exit_code, reason
    if status in _QUEUED:
        return ExecutionState.QUEUED, None, None
    if status in _RUNNING:
        return ExecutionState.RUNNING, None, None
    return ExecutionState.UNKNOWN, None, None


class BatchJobBackend(BaseBackend):
    """Runs AWS Batch jobs.

    Args:
        region: AWS region. ``None`` uses boto3's own resolution.
        job_queue: Default Batch job queue.
        job_definition: Default Batch job definition.
        client: Injected ``boto3.client("batch")`` for testing.

    Backend options, under the ``"aws"`` namespace: ``job_queue``,
    ``job_definition``, ``share_identifier``, ``scheduling_priority``.
    """

    def __init__(
        self,
        *,
        region: str | None = None,
        job_queue: str | None = None,
        job_definition: str | None = None,
        client: Any = None,
        name: str = "batch",
        tracked_ids: int = 10_000,
    ) -> None:
        self._region = region
        self._job_queue = job_queue
        self._job_definition = job_definition
        self._client = client
        self._name = name
        self._ids = ExternalIdIndex(capacity=tracked_ids)

    @property
    def name(self) -> str:
        return self._name

    @property
    def kind(self) -> ExecutionKind:
        return ExecutionKind.JOB

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(BATCH_CAPABILITIES, provider=self._name)

    def _batch(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - depends on the environment
                raise ConfigurationError(
                    "the AWS Batch backend needs boto3: pip install 'taskport-jobs[aws]'"
                ) from exc
            self._client = boto3.client("batch", region_name=self._region)
        return self._client

    def _required(self, spec: JobSpec, key: str, default: str | None) -> str:
        value = spec.options_for("aws").get(key, default)
        if not isinstance(value, str) or not value:
            raise ConfigurationError(
                f"AWS Batch needs {key!r}: set it on the backend or in "
                f"backend_options['aws'][{key!r}]"
            )
        return value

    # -- submission --------------------------------------------------------------- #
    def _submit(self, spec: ExecutionSpec) -> Execution:
        assert isinstance(spec, JobSpec)
        options = spec.options_for("aws")
        overrides: dict[str, Any] = {}
        if spec.argv:
            overrides["command"] = list(spec.argv)
        if spec.env:
            overrides["environment"] = [
                {"name": key, "value": value} for key, value in spec.env.items()
            ]
        requirements = _resource_requirements(spec)
        if requirements:
            overrides["resourceRequirements"] = requirements

        job_definition = self._required(spec, "job_definition", self._job_definition)
        job_queue = self._required(spec, "job_queue", self._job_queue)
        request: dict[str, Any] = {
            "jobName": _safe_job_name(spec.job),
            "jobQueue": job_queue,
            "jobDefinition": job_definition,
        }
        if overrides:
            request["containerOverrides"] = overrides
        if spec.parallelism > 1:
            request["arrayProperties"] = {"size": spec.parallelism}
        if spec.timeout.seconds is not None:
            request["timeout"] = {"attemptDurationSeconds": int(spec.timeout.seconds)}
        if spec.retry.enabled:
            request["retryStrategy"] = {"attempts": spec.retry.engine_attempts}
        for key in ("share_identifier", "scheduling_priority"):
            if key in options:
                request[_camel(key)] = options[key]

        try:
            response = self._batch().submit_job(**request)
        except Exception as exc:
            raise SubmissionError(
                f"AWS Batch could not submit {spec.job!r} to queue {job_queue!r}: {exc}",
                backend=self._name,
            ) from exc

        external_id = response.get("jobId") if isinstance(response, dict) else None
        execution_id = new_execution_id(ExecutionKind.JOB)
        self._ids.remember(str(execution_id), external_id)
        return Execution(
            id=execution_id,
            kind=ExecutionKind.JOB,
            backend=self._name,
            state=ExecutionState.QUEUED,
            name=spec.name,
            created_at=datetime.now(UTC),
            external_id=external_id,
            correlation=spec.correlation,
            provider_metadata=ProviderMetadata(
                provider="aws",
                provider_id=external_id,
                region=self._region,
                resource=job_definition,
                labels=dict(spec.labels),
            ),
            metadata={"profile": spec.profile, "job_queue": job_queue},
        )

    # -- observation ---------------------------------------------------------------- #
    def _describe(self, execution_id: ExecutionId) -> tuple[str, dict[str, Any]]:
        external_id = self._ids.resolve(str(execution_id))
        if external_id is None:
            raise ExecutionNotFound(
                f"{execution_id!r} was not submitted by this backend instance; pass the "
                "Batch jobId to look it up from another process",
                backend=self._name,
            )
        try:
            response = self._batch().describe_jobs(jobs=[external_id])
        except Exception as exc:
            raise BackendError(
                f"AWS Batch could not describe job {external_id!r}: {exc}", backend=self._name
            ) from exc
        jobs = (response.get("jobs") or []) if isinstance(response, dict) else []
        if not jobs:
            raise ExecutionNotFound(f"AWS Batch has no job {external_id!r}", backend=self._name)
        return external_id, jobs[0]

    def _get(self, execution_id: ExecutionId) -> Execution:
        external_id, job = self._describe(execution_id)
        state, exit_code, error = map_batch_state(job)
        return Execution(
            id=execution_id,
            kind=ExecutionKind.JOB,
            backend=self._name,
            state=state,
            name=str(job.get("jobName", "")),
            started_at=_millis(job.get("startedAt")),
            finished_at=_millis(job.get("stoppedAt")),
            external_id=external_id,
            attempt=len(job.get("attempts") or []) or 1,
            result=ExecutionResult(
                value=exit_code,
                error=error,
                error_type="JobFailed" if error else None,
                exit_code=exit_code,
                logs_uri=_log_stream(job),
            )
            if state.is_terminal
            else None,
            provider_metadata=ProviderMetadata(
                provider="aws",
                provider_id=external_id,
                region=self._region,
                resource=str(job.get("jobDefinition", "")),
            ),
        )

    def _result(self, execution_id: ExecutionId, *, timeout: float | None) -> ExecutionResult:
        execution = self._wait(execution_id, timeout=timeout)
        return execution.result or ExecutionResult()

    def _cancel(self, execution_id: ExecutionId) -> Execution:
        external_id = self._ids.resolve(str(execution_id))
        if external_id is None:
            raise ExecutionNotFound(
                f"{execution_id!r} was not submitted by this backend instance",
                backend=self._name,
            )
        try:
            self._batch().terminate_job(jobId=external_id, reason="cancelled via taskport")
        except Exception as exc:
            raise BackendError(
                f"AWS Batch could not terminate job {external_id!r}: {exc}", backend=self._name
            ) from exc
        return self._get(execution_id)


def _resource_requirements(spec: JobSpec) -> list[dict[str, str]]:
    """Translate portable resources into Batch's ``resourceRequirements``."""
    requirements: list[dict[str, str]] = []
    if spec.resources.cpu is not None:
        requirements.append({"type": "VCPU", "value": _vcpu(spec.resources.cpu)})
    if spec.resources.memory is not None:
        requirements.append({"type": "MEMORY", "value": _mib(spec.resources.memory)})
    if spec.resources.gpu > 0:
        requirements.append({"type": "GPU", "value": str(spec.resources.gpu)})
    return requirements


def _vcpu(cpu: str) -> str:
    """Kubernetes CPU quantity to Batch vCPU count: ``"2000m"`` becomes ``"2"``."""
    if cpu.endswith("m"):
        vcpus = float(cpu[:-1]) / 1000
        return str(int(vcpus)) if vcpus.is_integer() else str(vcpus)
    return cpu


def _mib(memory: str) -> str:
    """Kubernetes memory quantity to Batch MiB: ``"512Mi"`` becomes ``"512"``."""
    for suffix, factor in (("Gi", 1024), ("Mi", 1), ("G", 1000), ("M", 1)):
        if memory.endswith(suffix):
            return str(int(float(memory[: -len(suffix)]) * factor))
    return memory


def _safe_job_name(name: str) -> str:
    """Batch job names allow letters, digits, hyphen and underscore, up to 128."""
    cleaned = "".join(char if char.isalnum() or char in "-_" else "-" for char in name)
    return cleaned[:128] or "taskport-job"


def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(part.title() for part in rest)


def _millis(value: Any) -> datetime | None:
    """Batch reports epoch milliseconds; ``None`` when the phase has not started."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _log_stream(job: dict[str, Any]) -> str | None:
    container = job.get("container") or {}
    stream = container.get("logStreamName")
    return f"cloudwatch:/aws/batch/job:{stream}" if stream else None


def make_backend(**options: Any) -> BatchJobBackend:
    """Entry point for ``{"factory": "aws-batch", ...}`` configuration."""
    return BatchJobBackend(
        region=options.get("region"),
        job_queue=options.get("job_queue"),
        job_definition=options.get("job_definition"),
        client=options.get("client"),
        name=str(options.get("name", "batch")),
        tracked_ids=int(options.get("tracked_ids", 10_000)),
    )


__all__ = [
    "BATCH_CAPABILITIES",
    "BatchJobBackend",
    "make_backend",
    "map_batch_state",
]
