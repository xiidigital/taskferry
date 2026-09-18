"""Kubernetes `JobBackend`.

```mermaid
flowchart LR
    SPEC["JobSpec<br/>image · argv · resources"]
    B["KubernetesJobBackend"]
    API["batch/v1 Job"]
    POD["pod(s)"]

    SPEC --> B --> API --> POD
```

Kubernetes is the backend where the portable resource vocabulary needs no
translation at all: ``Resources(cpu="1000m", memory="512Mi")`` is *already*
Kubernetes quantity syntax. That is not a coincidence — it is why
:class:`~taskferry.specs.Resources` was defined that way, so the least lossy
mapping is the default and every other backend converts outwards from a
well-specified format instead of inwards from an ambiguous one.

Authentication follows the cluster's own conventions: in-cluster ServiceAccount
config when running inside a pod, the local kubeconfig otherwise. The client is
imported lazily and injectable.

What this deliberately does not do: watch pods, stream logs, manage a controller,
or garbage-collect finished Jobs. Kubernetes has ``ttlSecondsAfterFinished`` and
a control plane for exactly that.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from taskferry.capabilities import Capability, CapabilitySet
from taskferry.core.ids import new_id
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

KUBERNETES_CAPABILITIES = frozenset(
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
        Capability.GPU,
        Capability.ENVIRONMENT,
    }
)

GPU_RESOURCE = "nvidia.com/gpu"
"""Extended resource name for GPUs. Override per-spec via backend options."""


def map_job_state(status: Any, *, completions: int = 1) -> tuple[ExecutionState, str | None]:
    """Map a ``V1JobStatus`` (or a duck-typed equivalent) to a portable state.

    ``completions`` matters for array jobs: one succeeded pod out of four is
    still ``RUNNING``, and reporting ``SUCCEEDED`` there would be a lie that only
    shows up when someone reads three-quarters of the output.
    """
    succeeded = _count(status, "succeeded")
    failed = _count(status, "failed")
    active = _count(status, "active")

    if succeeded >= max(completions, 1):
        return ExecutionState.SUCCEEDED, None
    if failed > 0 and active == 0:
        reason = _failure_reason(status)
        if reason and "deadline" in reason.lower():
            return ExecutionState.TIMED_OUT, reason
        return ExecutionState.FAILED, reason or f"{failed} pod(s) failed"
    if active > 0:
        return ExecutionState.RUNNING, None
    if succeeded > 0:
        return ExecutionState.RUNNING, None  # partial completion of an array job
    return ExecutionState.QUEUED, None


def _count(status: Any, field: str) -> int:
    try:
        return int(getattr(status, field, 0) or 0)
    except (TypeError, ValueError):  # pragma: no cover - hostile fake
        return 0


def _failure_reason(status: Any) -> str | None:
    for condition in getattr(status, "conditions", None) or []:
        if str(getattr(condition, "type", "")) == "Failed":
            return str(getattr(condition, "reason", "") or getattr(condition, "message", ""))
    return None


class KubernetesJobBackend(BaseBackend):
    """Creates and observes Kubernetes batch/v1 Jobs.

    Args:
        namespace: Namespace the Jobs are created in.
        api: Injected ``kubernetes.client.BatchV1Api`` for testing.
        service_account: ServiceAccount name for the pods.
        ttl_seconds_after_finished: Let Kubernetes clean finished Jobs up. Set to
            ``None`` to keep them, and take on the cleanup yourself.

    Backend options, under the ``"kubernetes"`` namespace: ``node_selector``,
    ``tolerations``, ``gpu_resource``, ``labels``, ``annotations``.
    """

    def __init__(
        self,
        *,
        namespace: str = "default",
        api: Any = None,
        service_account: str | None = None,
        ttl_seconds_after_finished: int | None = 3600,
        name: str = "kubernetes",
        tracked_ids: int = 10_000,
    ) -> None:
        self._namespace = namespace
        self._api = api
        self._service_account = service_account
        self._ttl = ttl_seconds_after_finished
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
        return CapabilitySet(KUBERNETES_CAPABILITIES, provider=self._name)

    @property
    def namespace(self) -> str:
        return self._namespace

    def _batch_api(self) -> Any:
        if self._api is None:
            try:
                from kubernetes import client, config
            except ImportError as exc:  # pragma: no cover - depends on the environment
                raise ConfigurationError(
                    "the Kubernetes backend needs the client: "
                    "pip install 'taskferry-jobs[kubernetes]'"
                ) from exc
            try:
                config.load_incluster_config()
            except Exception:
                # Not in a pod — fall back to the developer's kubeconfig.
                config.load_kube_config()
            self._api = client.BatchV1Api()
        return self._api

    # -- manifest ------------------------------------------------------------------ #
    def build_manifest(self, spec: JobSpec, job_name: str) -> dict[str, Any]:
        """The batch/v1 Job this spec becomes. Pure, so it is easy to assert on."""
        if not spec.image:
            raise SubmissionError(
                f"job {spec.job!r} has no image; Kubernetes needs one to create a pod",
                backend=self._name,
            )
        options = spec.options_for("kubernetes")

        container: dict[str, Any] = {"name": _dns_name(spec.job), "image": spec.image}
        if spec.command:
            container["command"] = list(spec.command)
        if spec.args:
            container["args"] = list(spec.args)
        if spec.env:
            container["env"] = [{"name": k, "value": v} for k, v in spec.env.items()]
        if spec.working_dir:
            container["workingDir"] = spec.working_dir
        limits = self._limits(spec, str(options.get("gpu_resource", GPU_RESOURCE)))
        if limits:
            # Requests and limits are set to the same values: a batch job that
            # gets throttled or OOM-killed halfway through is worse than one that
            # waits for capacity it can actually use.
            container["resources"] = {"limits": limits, "requests": dict(limits)}

        pod_spec: dict[str, Any] = {"containers": [container], "restartPolicy": "Never"}
        if self._service_account:
            pod_spec["serviceAccountName"] = self._service_account
        if options.get("node_selector"):
            pod_spec["nodeSelector"] = dict(options["node_selector"])  # type: ignore[arg-type]
        if options.get("tolerations"):
            pod_spec["tolerations"] = list(options["tolerations"])  # type: ignore[arg-type]

        job_spec: dict[str, Any] = {
            "template": {
                "metadata": {
                    "labels": {**dict(spec.labels), "taskferry.dev/job": _dns_name(spec.job)}
                },
                "spec": pod_spec,
            },
            # backoffLimit counts retries, RetryPolicy counts total attempts.
            "backoffLimit": max(spec.retry.engine_attempts - 1, 0),
        }
        if spec.parallelism > 1:
            job_spec["parallelism"] = spec.parallelism
            job_spec["completions"] = spec.parallelism
        if spec.timeout.seconds is not None:
            job_spec["activeDeadlineSeconds"] = int(spec.timeout.seconds)
        if self._ttl is not None:
            job_spec["ttlSecondsAfterFinished"] = self._ttl

        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": job_name,
                "labels": {**dict(spec.labels), "app.kubernetes.io/managed-by": "taskferry"},
                "annotations": dict(options.get("annotations") or {}),  # type: ignore[arg-type]
            },
            "spec": job_spec,
        }

    @staticmethod
    def _limits(spec: JobSpec, gpu_resource: str) -> dict[str, str]:
        limits: dict[str, str] = {}
        if spec.resources.cpu is not None:
            limits["cpu"] = spec.resources.cpu
        if spec.resources.memory is not None:
            limits["memory"] = spec.resources.memory
        if spec.resources.gpu > 0:
            limits[gpu_resource] = str(spec.resources.gpu)
        return limits

    # -- submission ------------------------------------------------------------------ #
    def _submit(self, spec: ExecutionSpec) -> Execution:
        assert isinstance(spec, JobSpec)
        # Kubernetes object names must be unique and DNS-1123; a short random
        # suffix keeps repeated runs of the same job from colliding.
        job_name = f"{_dns_name(spec.job)[:50]}-{new_id('k')[2:10]}"
        manifest = self.build_manifest(spec, job_name)
        try:
            self._batch_api().create_namespaced_job(namespace=self._namespace, body=manifest)
        except Exception as exc:
            raise SubmissionError(
                f"Kubernetes could not create job {job_name!r} in namespace "
                f"{self._namespace!r}: {exc}",
                backend=self._name,
            ) from exc

        execution_id = new_execution_id(ExecutionKind.JOB)
        self._ids.remember(str(execution_id), job_name)
        return Execution(
            id=execution_id,
            kind=ExecutionKind.JOB,
            backend=self._name,
            state=ExecutionState.QUEUED,
            name=spec.name,
            created_at=datetime.now(UTC),
            external_id=job_name,
            correlation=spec.correlation,
            provider_metadata=ProviderMetadata(
                provider="kubernetes",
                provider_id=job_name,
                resource=f"{self._namespace}/{job_name}",
                labels=dict(spec.labels),
            ),
            metadata={"profile": spec.profile, "namespace": self._namespace},
        )

    # -- observation -------------------------------------------------------------------- #
    def _get(self, execution_id: ExecutionId) -> Execution:
        job_name = self._job_name(execution_id)
        try:
            job = self._batch_api().read_namespaced_job_status(
                name=job_name, namespace=self._namespace
            )
        except Exception as exc:
            if _is_not_found(exc):
                raise ExecutionNotFound(
                    f"Kubernetes has no job {self._namespace}/{job_name}", backend=self._name
                ) from exc
            raise BackendError(
                f"Kubernetes could not read job {job_name!r}: {exc}", backend=self._name
            ) from exc

        completions = int(getattr(getattr(job, "spec", None), "completions", 1) or 1)
        state, error = map_job_state(getattr(job, "status", job), completions=completions)
        return Execution(
            id=execution_id,
            kind=ExecutionKind.JOB,
            backend=self._name,
            state=state,
            name=job_name,
            started_at=getattr(getattr(job, "status", None), "start_time", None),
            finished_at=getattr(getattr(job, "status", None), "completion_time", None),
            external_id=job_name,
            result=ExecutionResult(
                error=error,
                error_type="JobFailed" if error else None,
                logs_uri=f"kubectl logs -n {self._namespace} job/{job_name}",
            )
            if state.is_terminal
            else None,
            provider_metadata=ProviderMetadata(
                provider="kubernetes",
                provider_id=job_name,
                resource=f"{self._namespace}/{job_name}",
            ),
        )

    def _cancel(self, execution_id: ExecutionId) -> Execution:
        job_name = self._job_name(execution_id)
        snapshot = self._get(execution_id)
        try:
            # Background propagation deletes the pods too; without it the Job
            # object goes away and its pods keep running.
            self._batch_api().delete_namespaced_job(
                name=job_name, namespace=self._namespace, propagation_policy="Background"
            )
        except Exception as exc:
            raise BackendError(
                f"Kubernetes could not delete job {job_name!r}: {exc}", backend=self._name
            ) from exc
        return snapshot.evolve(state=ExecutionState.CANCELLED, finished_at=datetime.now(UTC))

    def _job_name(self, execution_id: ExecutionId) -> str:
        name = self._ids.resolve(str(execution_id))
        if name is None:
            raise ExecutionNotFound(
                f"{execution_id!r} was not submitted by this backend instance; pass the "
                "Kubernetes job name to look it up from another process",
                backend=self._name,
            )
        return name


def _dns_name(value: str) -> str:
    """Reduce a name to RFC 1123: lowercase alphanumerics and hyphens."""
    cleaned = "".join(char if char.isalnum() else "-" for char in value.lower())
    return cleaned.strip("-") or "taskferry-job"


def _is_not_found(exc: Exception) -> bool:
    """Whether a Kubernetes ``ApiException`` means 404, without importing the client."""
    return getattr(exc, "status", None) == 404


def make_backend(**options: Any) -> KubernetesJobBackend:
    """Entry point for ``{"factory": "kubernetes", ...}`` configuration."""
    ttl = options.get("ttl_seconds_after_finished", 3600)
    return KubernetesJobBackend(
        namespace=str(options.get("namespace", "default")),
        api=options.get("api"),
        service_account=options.get("service_account"),
        ttl_seconds_after_finished=None if ttl is None else int(ttl),
        name=str(options.get("name", "kubernetes")),
        tracked_ids=int(options.get("tracked_ids", 10_000)),
    )


__all__ = [
    "GPU_RESOURCE",
    "KUBERNETES_CAPABILITIES",
    "KubernetesJobBackend",
    "make_backend",
    "map_job_state",
]
