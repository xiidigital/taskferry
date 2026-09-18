"""Azure Container Apps Jobs `JobBackend`.

```mermaid
flowchart LR
    SPEC["JobSpec"]
    B["ContainerAppsJobBackend"]
    API["jobs.begin_start"]
    EXEC["job execution"]

    SPEC --> B --> API --> EXEC
```

Azure's model is closest to Cloud Run's: a Job resource is declared ahead of
time, and a submission starts an execution of it with a template override. So the
capability profile is the same shape — environment, parallelism and timeout are
overridable per execution; GPU is not, and is therefore not advertised.

CPU and memory *are* overridable through the template, so unlike Cloud Run this
backend does advertise them. The two adapters sitting side by side with different
honest answers is the capability model doing its job: nothing had to be reduced
to a lowest common denominator for both to be usable through one API.

The management client is imported lazily and injectable; SDK responses are read
defensively because the Azure SDK's shapes shift between versions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from taskferry.capabilities import Capability, CapabilitySet
from taskferry.core.provider import ProviderMetadata
from taskferry.errors import BackendError, ConfigurationError, ExecutionNotFound, SubmissionError
from taskferry.execution import (
    Execution,
    ExecutionId,
    ExecutionKind,
    ExecutionResult,
    ExecutionState,
    new_execution_id,
)
from taskferry.ports import BaseBackend
from taskferry.specs import ExecutionSpec, JobSpec
from taskferry.tracking import ExternalIdIndex

CONTAINER_APPS_CAPABILITIES = frozenset(
    {
        Capability.SUBMIT,
        Capability.STATE,
        Capability.CANCEL,
        Capability.LOGS,
        Capability.TIMEOUT,
        Capability.PARALLELISM,
        Capability.RETRY,
        Capability.CPU,
        Capability.MEMORY,
        Capability.ENVIRONMENT,
    }
)

_STATE_MAP: dict[str, ExecutionState] = {
    "succeeded": ExecutionState.SUCCEEDED,
    "failed": ExecutionState.FAILED,
    "stopped": ExecutionState.CANCELLED,
    "canceled": ExecutionState.CANCELLED,
    "cancelled": ExecutionState.CANCELLED,
    "running": ExecutionState.RUNNING,
    "processing": ExecutionState.RUNNING,
    "degraded": ExecutionState.RUNNING,
    "unknown": ExecutionState.UNKNOWN,
}


def map_execution_state(raw: str | None) -> ExecutionState:
    """Map an Azure execution status string to a portable state. Pure.

    An unrecognised status becomes ``UNKNOWN`` rather than a guess: a new Azure
    status must not silently become "succeeded".
    """
    if not raw:
        return ExecutionState.UNKNOWN
    return _STATE_MAP.get(str(raw).strip().lower(), ExecutionState.UNKNOWN)


class ContainerAppsJobBackend(BaseBackend):
    """Starts and observes Azure Container Apps Job executions.

    Args:
        subscription_id: Azure subscription id.
        resource_group: Resource group holding the Job resources.
        client: Injected ``ContainerAppsAPIClient`` for testing.
        credential: Injected credential. ``DefaultAzureCredential`` otherwise,
            which picks up Managed Identity in Azure and the developer's
            ``az login`` locally.

    Backend options, under the ``"azure"`` namespace: ``job_name`` to target a
    Job resource whose name differs from ``JobSpec.job``.
    """

    def __init__(
        self,
        *,
        subscription_id: str | None = None,
        resource_group: str | None = None,
        client: Any = None,
        credential: Any = None,
        name: str = "containerapps",
        tracked_ids: int = 10_000,
    ) -> None:
        if not subscription_id or not resource_group:
            raise ConfigurationError(
                "ContainerAppsJobBackend needs both 'subscription_id' and 'resource_group'"
            )
        self._subscription_id = subscription_id
        self._resource_group = resource_group
        self._client = client
        self._credential = credential
        self._name = name
        self._ids = ExternalIdIndex(capacity=tracked_ids)
        # Azure needs the parent Job name to look an execution up, and an
        # execution name alone does not carry it.
        self._job_names: dict[str, str] = {}

    @property
    def name(self) -> str:
        return self._name

    @property
    def kind(self) -> ExecutionKind:
        return ExecutionKind.JOB

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(CONTAINER_APPS_CAPABILITIES, provider=self._name)

    def _mgmt(self) -> Any:
        if self._client is None:
            try:
                from azure.identity import DefaultAzureCredential
                from azure.mgmt.appcontainers import ContainerAppsAPIClient
            except ImportError as exc:  # pragma: no cover - depends on the environment
                raise ConfigurationError(
                    "the Azure Container Apps backend needs the Azure SDK: "
                    "pip install 'taskferry-jobs[azure]'"
                ) from exc
            credential = self._credential or DefaultAzureCredential()
            self._client = ContainerAppsAPIClient(credential, self._subscription_id)
        return self._client

    # -- template -------------------------------------------------------------------- #
    def build_template(self, spec: JobSpec) -> dict[str, Any]:
        """The execution template override this spec becomes. Pure."""
        container: dict[str, Any] = {"name": spec.job, "image": spec.image}
        if spec.command:
            container["command"] = list(spec.command)
        if spec.args:
            container["args"] = list(spec.args)
        if spec.env:
            container["env"] = [{"name": k, "value": v} for k, v in spec.env.items()]
        resources: dict[str, Any] = {}
        if spec.resources.cpu is not None:
            resources["cpu"] = _cores(spec.resources.cpu)
        if spec.resources.memory is not None:
            resources["memory"] = _gib(spec.resources.memory)
        if resources:
            container["resources"] = resources

        template: dict[str, Any] = {"containers": [container]}
        if spec.parallelism > 1:
            template["parallelism"] = spec.parallelism
        if spec.timeout.seconds is not None:
            template["replicaTimeout"] = int(spec.timeout.seconds)
        if spec.retry.enabled:
            template["replicaRetryLimit"] = max(spec.retry.engine_attempts - 1, 0)
        return template

    # -- submission ------------------------------------------------------------------ #
    def _submit(self, spec: ExecutionSpec) -> Execution:
        assert isinstance(spec, JobSpec)
        job_name = str(spec.options_for("azure").get("job_name") or spec.job)
        try:
            poller = self._mgmt().jobs.begin_start(
                self._resource_group, job_name, self.build_template(spec)
            )
            started = poller.result() if hasattr(poller, "result") else poller
        except Exception as exc:
            raise SubmissionError(
                f"Azure could not start job {job_name!r} in resource group "
                f"{self._resource_group!r}: {exc}",
                backend=self._name,
            ) from exc

        external_id = getattr(started, "name", None)
        execution_id = new_execution_id(ExecutionKind.JOB)
        self._ids.remember(str(execution_id), external_id)
        self._job_names[str(execution_id)] = job_name
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
                provider="azure",
                provider_id=external_id,
                resource=f"{self._resource_group}/{job_name}",
                labels=dict(spec.labels),
            ),
            metadata={"profile": spec.profile, "job_name": job_name},
        )

    # -- observation ------------------------------------------------------------------- #
    def _locate(self, execution_id: ExecutionId) -> tuple[str, str]:
        external_id = self._ids.resolve(str(execution_id))
        job_name = self._job_names.get(str(execution_id))
        if external_id is None or job_name is None:
            raise ExecutionNotFound(
                f"{execution_id!r} was not submitted by this backend instance; Azure needs "
                "both the parent job name and the execution name to look one up",
                backend=self._name,
            )
        return job_name, external_id

    def _get(self, execution_id: ExecutionId) -> Execution:
        job_name, external_id = self._locate(execution_id)
        try:
            executions = self._mgmt().jobs_executions.list(self._resource_group, job_name)
        except Exception as exc:
            raise BackendError(
                f"Azure could not list executions of {job_name!r}: {exc}", backend=self._name
            ) from exc

        for candidate in executions:
            if getattr(candidate, "name", None) != external_id:
                continue
            state = map_execution_state(_status_of(candidate))
            return Execution(
                id=execution_id,
                kind=ExecutionKind.JOB,
                backend=self._name,
                state=state,
                name=job_name,
                started_at=getattr(candidate, "start_time", None),
                finished_at=getattr(candidate, "end_time", None),
                external_id=external_id,
                result=ExecutionResult(
                    error=None if state is ExecutionState.SUCCEEDED else f"execution {state.value}",
                    error_type=None if state is ExecutionState.SUCCEEDED else "JobFailed",
                )
                if state.is_terminal
                else None,
                provider_metadata=ProviderMetadata(
                    provider="azure",
                    provider_id=external_id,
                    resource=f"{self._resource_group}/{job_name}",
                ),
            )
        raise ExecutionNotFound(
            f"Azure has no execution {external_id!r} of job {job_name!r}", backend=self._name
        )

    def _cancel(self, execution_id: ExecutionId) -> Execution:
        job_name, external_id = self._locate(execution_id)
        try:
            self._mgmt().jobs.begin_stop_execution(self._resource_group, job_name, external_id)
        except Exception as exc:
            raise BackendError(
                f"Azure could not stop execution {external_id!r}: {exc}", backend=self._name
            ) from exc
        return self._get(execution_id)


def _status_of(execution: Any) -> str | None:
    """Read the status, which the SDK puts in one of two places by version."""
    direct = getattr(execution, "status", None)
    if direct:
        return str(direct)
    nested = getattr(getattr(execution, "properties", None), "status", None)
    return str(nested) if nested else None


def _cores(cpu: str) -> float:
    """Kubernetes CPU quantity to Azure's fractional cores: ``"500m"`` -> ``0.5``."""
    return float(cpu[:-1]) / 1000 if cpu.endswith("m") else float(cpu)


def _gib(memory: str) -> str:
    """Kubernetes memory quantity to Azure's ``"<n>Gi"`` string."""
    for suffix, factor in (("Gi", 1.0), ("Mi", 1 / 1024), ("G", 1000 / 1024), ("M", 1 / 1024)):
        if memory.endswith(suffix):
            return f"{float(memory[: -len(suffix)]) * factor:.2f}".rstrip("0").rstrip(".") + "Gi"
    return memory


def make_backend(**options: Any) -> ContainerAppsJobBackend:
    """Entry point for ``{"factory": "containerapps", ...}`` configuration."""
    return ContainerAppsJobBackend(
        subscription_id=options.get("subscription_id"),
        resource_group=options.get("resource_group"),
        client=options.get("client"),
        credential=options.get("credential"),
        name=str(options.get("name", "containerapps")),
        tracked_ids=int(options.get("tracked_ids", 10_000)),
    )


__all__ = [
    "CONTAINER_APPS_CAPABILITIES",
    "ContainerAppsJobBackend",
    "make_backend",
    "map_execution_state",
]
