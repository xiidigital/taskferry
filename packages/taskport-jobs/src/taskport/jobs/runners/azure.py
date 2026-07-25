"""ContainerAppsJobsRunner — run a workload as an Azure Container Apps Job.

Targets ``azure-mgmt-appcontainers`` with ``azure-identity``'s
``DefaultAzureCredential`` / Managed Identity (ADR-0007). The management client
is imported lazily and can be injected for testing. Execution start/stop/list are
accessed defensively so the adapter tolerates minor SDK shape differences and is
easy to fake.
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

_AZURE_CAPABILITIES = frozenset(
    {
        JobCapability.STATUS,
        JobCapability.CANCEL,
        JobCapability.LOGS,
        JobCapability.TIMEOUT,
        JobCapability.PARALLELISM,
        JobCapability.CPU_OVERRIDE,
        JobCapability.MEMORY_OVERRIDE,
        JobCapability.ENVIRONMENT_OVERRIDE,
    }
)

_STATUS_MAP = {
    "succeeded": JobStatus.SUCCEEDED,
    "failed": JobStatus.FAILED,
    "stopped": JobStatus.CANCELLED,
    "running": JobStatus.RUNNING,
    "processing": JobStatus.RUNNING,
}


def map_aca_status(raw: str | None) -> JobStatus:
    """Map an Azure Container Apps execution status string to portable status."""
    if not raw:
        return JobStatus.UNKNOWN
    return _STATUS_MAP.get(raw.strip().lower(), JobStatus.UNKNOWN)


class ContainerAppsJobsRunner(BaseJobRunner):
    """Runs Azure Container Apps Job executions."""

    def __init__(
        self,
        *,
        subscription_id: str,
        resource_group: str,
        client: Any = None,
        credential: Any = None,
    ) -> None:
        self._subscription_id = subscription_id
        self._resource_group = resource_group
        self._client = client
        self._credential = credential

    @property
    def provider(self) -> str:
        return "azure"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(_AZURE_CAPABILITIES, provider="azure")

    def _mgmt(self) -> Any:
        if self._client is None:
            try:
                from azure.identity import DefaultAzureCredential
                from azure.mgmt.appcontainers import (
                    ContainerAppsAPIClient,
                )
            except ImportError as exc:  # pragma: no cover
                raise ProviderError(
                    "azure-mgmt-appcontainers and azure-identity are required for "
                    "ContainerAppsJobsRunner; install taskport-jobs[azure]",
                    provider="azure",
                ) from exc
            credential = self._credential or DefaultAzureCredential()
            self._client = ContainerAppsAPIClient(credential, self._subscription_id)
        return self._client

    def _template(self, spec: JobSpec) -> dict[str, Any]:
        container: dict[str, Any] = {"name": spec.name, "image": spec.image}
        if spec.command:
            container["args"] = list(spec.command)
        if spec.env:
            container["env"] = [{"name": k, "value": v} for k, v in spec.env.items()]
        resources: dict[str, Any] = {}
        if spec.resources.cpu is not None:
            resources["cpu"] = spec.resources.cpu
        if spec.resources.memory is not None:
            resources["memory"] = spec.resources.memory
        if resources:
            container["resources"] = resources
        template: dict[str, Any] = {"containers": [container]}
        if spec.parallelism > 1:
            template["parallelism"] = spec.parallelism
        return template

    def _submit(self, spec: JobSpec) -> JobHandle:
        try:
            poller = self._mgmt().jobs.begin_start(
                self._resource_group, spec.name, self._template(spec)
            )
            execution = poller.result() if hasattr(poller, "result") else poller
        except Exception as exc:
            raise ProviderError(f"begin_start failed: {exc}", provider="azure") from exc
        execution_name = getattr(execution, "name", None)
        metadata = ProviderMetadata(
            provider="azure",
            provider_id=execution_name,
            resource=f"{self._resource_group}/{spec.name}",
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
        job_name = handle.name
        if not execution_name:
            return JobResult(handle=handle, status=JobStatus.UNKNOWN)
        try:
            executions = self._mgmt().jobs_executions.list(self._resource_group, job_name)
        except Exception as exc:
            raise ProviderError(f"jobs_executions.list failed: {exc}", provider="azure") from exc
        for execution in executions:
            if getattr(execution, "name", None) == execution_name:
                raw = getattr(execution, "status", None) or getattr(
                    getattr(execution, "properties", None), "status", None
                )
                return JobResult(handle=handle, status=map_aca_status(raw))
        return JobResult(handle=handle, status=JobStatus.UNKNOWN)

    def _cancel(self, handle: JobHandle) -> None:
        execution_name = handle.provider_metadata.provider_id
        if not execution_name:
            return
        try:
            self._mgmt().jobs.begin_stop_execution(
                self._resource_group, handle.name, execution_name
            )
        except Exception as exc:
            raise ProviderError(f"begin_stop_execution failed: {exc}", provider="azure") from exc


def make_container_apps_jobs_runner(**kwargs: object) -> ContainerAppsJobsRunner:
    return ContainerAppsJobsRunner(
        subscription_id=str(kwargs["subscription_id"]),
        resource_group=str(kwargs["resource_group"]),
    )


__all__ = [
    "ContainerAppsJobsRunner",
    "make_container_apps_jobs_runner",
    "map_aca_status",
]
