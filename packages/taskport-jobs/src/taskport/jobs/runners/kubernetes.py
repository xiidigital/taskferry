"""KubernetesJobRunner — run a workload as a Kubernetes Job.

Uses in-cluster config by default (a mounted ``ServiceAccount``), falling back to
the local kubeconfig for development (ADR-0007). The BatchV1 API client is
imported lazily and can be injected for testing.
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
from ..errors import JobError
from ..models import JobHandle, JobResult, JobSpec, JobStatus
from ..runner import BaseJobRunner

_K8S_CAPABILITIES = frozenset(
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


def map_k8s_status(status: Any) -> tuple[JobStatus, str | None]:
    """Map a ``V1JobStatus`` (or a duck-typed equivalent) to portable status."""
    succeeded = int(getattr(status, "succeeded", 0) or 0)
    failed = int(getattr(status, "failed", 0) or 0)
    active = int(getattr(status, "active", 0) or 0)
    if succeeded > 0:
        return JobStatus.SUCCEEDED, None
    if failed > 0:
        return JobStatus.FAILED, f"{failed} pod(s) failed"
    if active > 0:
        return JobStatus.RUNNING, None
    return JobStatus.PENDING, None


class KubernetesJobRunner(BaseJobRunner):
    """Runs Kubernetes Jobs."""

    def __init__(
        self,
        *,
        namespace: str = "default",
        api: Any = None,
        service_account: str | None = None,
    ) -> None:
        self._namespace = namespace
        self._api = api
        self._service_account = service_account

    @property
    def provider(self) -> str:
        return "kubernetes"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(_K8S_CAPABILITIES, provider="kubernetes")

    def _batch_api(self) -> Any:
        if self._api is None:
            try:
                from kubernetes import client, config
            except ImportError as exc:  # pragma: no cover
                raise ProviderError(
                    "kubernetes is required for KubernetesJobRunner; "
                    "install taskport-jobs[kubernetes]",
                    provider="kubernetes",
                ) from exc
            try:
                config.load_incluster_config()
            except Exception:
                config.load_kube_config()
            self._api = client.BatchV1Api()
        return self._api

    def _job_body(self, spec: JobSpec, job_name: str) -> dict[str, Any]:
        if not spec.image:
            raise JobError("KubernetesJobRunner requires JobSpec.image")
        container: dict[str, Any] = {"name": spec.name, "image": spec.image}
        if spec.command:
            container["args"] = list(spec.command)
        if spec.env:
            container["env"] = [{"name": k, "value": v} for k, v in spec.env.items()]
        limits = self._resource_limits(spec)
        if limits:
            container["resources"] = {"limits": limits, "requests": dict(limits)}
        pod_spec: dict[str, Any] = {"containers": [container], "restartPolicy": "Never"}
        if self._service_account:
            pod_spec["serviceAccountName"] = self._service_account
        job_spec: dict[str, Any] = {
            "template": {"spec": pod_spec},
            "backoffLimit": spec.max_retries,
        }
        if spec.parallelism > 1:
            job_spec["parallelism"] = spec.parallelism
            job_spec["completions"] = spec.parallelism
        if spec.timeout is not None:
            job_spec["activeDeadlineSeconds"] = int(spec.timeout)
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": job_name, "labels": dict(spec.labels)},
            "spec": job_spec,
        }

    def _submit(self, spec: JobSpec) -> JobHandle:
        # Kubernetes names must be DNS-1123; suffix keeps them unique per run.
        job_name = f"{spec.name}-{new_id('k')[2:10]}".lower()
        body = self._job_body(spec, job_name)
        try:
            self._batch_api().create_namespaced_job(namespace=self._namespace, body=body)
        except Exception as exc:
            raise ProviderError(
                f"create_namespaced_job failed: {exc}", provider="kubernetes"
            ) from exc
        metadata = ProviderMetadata(
            provider="kubernetes",
            provider_id=job_name,
            resource=f"{self._namespace}/{job_name}",
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
        job_name = handle.provider_metadata.provider_id
        if not job_name:
            return JobResult(handle=handle, status=JobStatus.UNKNOWN)
        try:
            job = self._batch_api().read_namespaced_job_status(
                name=job_name, namespace=self._namespace
            )
        except Exception as exc:
            raise ProviderError(
                f"read_namespaced_job_status failed: {exc}", provider="kubernetes"
            ) from exc
        status, error = map_k8s_status(getattr(job, "status", job))
        return JobResult(handle=handle, status=status, error=error)

    def _cancel(self, handle: JobHandle) -> None:
        job_name = handle.provider_metadata.provider_id
        if not job_name:
            return
        try:
            self._batch_api().delete_namespaced_job(
                name=job_name,
                namespace=self._namespace,
                propagation_policy="Background",
            )
        except Exception as exc:
            raise ProviderError(
                f"delete_namespaced_job failed: {exc}", provider="kubernetes"
            ) from exc

    @staticmethod
    def _resource_limits(spec: JobSpec) -> dict[str, str]:
        limits: dict[str, str] = {}
        if spec.resources.cpu is not None:
            limits["cpu"] = spec.resources.cpu
        if spec.resources.memory is not None:
            limits["memory"] = spec.resources.memory
        if spec.resources.gpu > 0:
            limits["nvidia.com/gpu"] = str(spec.resources.gpu)
        return limits


def make_kubernetes_job_runner(**kwargs: object) -> KubernetesJobRunner:
    return KubernetesJobRunner(
        namespace=str(kwargs.get("namespace", "default")),
        service_account=kwargs.get("service_account"),  # type: ignore[arg-type]
    )


__all__ = [
    "KubernetesJobRunner",
    "make_kubernetes_job_runner",
    "map_k8s_status",
]
