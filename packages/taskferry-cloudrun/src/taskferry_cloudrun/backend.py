"""The Cloud Run Jobs `JobBackend`.

State mapping
-------------

Cloud Run reports an execution as a set of counters, not a status field, so the
portable state is derived:

```mermaid
flowchart TD
    E["Execution counters<br/>task_count · succeeded · failed · running · cancelled"]

    E -->|"cancelled > 0"| C["CANCELLED"]
    E -->|"completion_time & failed > 0"| F["FAILED"]
    E -->|"completion_time & succeeded >= task_count"| S["SUCCEEDED"]
    E -->|"no completion_time & running > 0"| R["RUNNING"]
    E -->|"no completion_time & running = 0"| Q["QUEUED"]
```

:func:`map_execution_state` is a pure function of those counters, so the mapping
is unit-tested against plain objects — no emulator, no project, no credentials.

Capabilities, and why they stop where they do
---------------------------------------------

Cloud Run's per-execution ``overrides`` cover container args, env, task count and
timeout. They do **not** cover CPU, memory or GPU: those belong to the Job
resource, which you deploy separately. So this backend advertises ``ENVIRONMENT``,
``PARALLELISM`` and ``TIMEOUT``, and deliberately does not advertise ``CPU``,
``MEMORY`` or ``GPU``.

The consequence is exactly the intended one: submitting a spec that asks for
``Resources(gpu=1)`` raises :class:`~taskferry.errors.UnsupportedCapability`
instead of running the workload on a CPU and returning results that look fine.
Route GPU work at a backend that really allocates GPUs.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from taskferry.capabilities import Capability, CapabilitySet
from taskferry.core.provider import ProviderMetadata
from taskferry.errors import (
    BackendError,
    ConfigurationError,
    ExecutionNotFound,
    SubmissionError,
)
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
from taskferry.tracking import ExternalIdIndex, has_prefix

CLOUD_RUN_CAPABILITIES = frozenset(
    {
        Capability.SUBMIT,
        Capability.STATE,
        Capability.CANCEL,
        Capability.LOGS,
        Capability.TIMEOUT,
        Capability.PARALLELISM,
        Capability.ENVIRONMENT,
        Capability.RETRY,
    }
)


def map_execution_state(execution: Any) -> tuple[ExecutionState, str | None]:
    """Map a Cloud Run ``Execution`` to a portable state and error message.

    Reads every field defensively via :func:`getattr`, which keeps it working
    against both real protobuf messages and the small fakes the tests use, and
    stops a field being renamed upstream from turning into a crash.
    """
    task_count = _count(execution, "task_count")
    succeeded = _count(execution, "succeeded_count")
    failed = _count(execution, "failed_count")
    running = _count(execution, "running_count")
    cancelled = _count(execution, "cancelled_count")
    completion_time = getattr(execution, "completion_time", None)

    if cancelled > 0:
        return ExecutionState.CANCELLED, None
    if completion_time:
        if failed > 0:
            return ExecutionState.FAILED, f"{failed} of {task_count} task(s) failed"
        if task_count > 0 and succeeded >= task_count:
            return ExecutionState.SUCCEEDED, None
        return ExecutionState.FAILED, "execution completed without all tasks succeeding"
    if running > 0:
        return ExecutionState.RUNNING, None
    # Accepted by Cloud Run, no task running yet: queued, not "unknown".
    return ExecutionState.QUEUED, None


def _count(execution: Any, field: str) -> int:
    try:
        return int(getattr(execution, field, 0) or 0)
    except (TypeError, ValueError):  # pragma: no cover - hostile fake
        return 0


class CloudRunJobBackend(BaseBackend):
    """Runs Cloud Run Job executions.

    Args:
        project: GCP project id.
        location: Region the Job resources live in (``"europe-west1"``).
        jobs_client: Injected ``run_v2.JobsClient``. Built lazily when omitted.
        executions_client: Injected ``run_v2.ExecutionsClient``.
        name: Backend name used in routing and errors.

    Backend options, under the ``"cloudrun"`` namespace::

        JobSpec(
            job="build-cog",
            backend_options=BackendOptions({"cloudrun": {
                "job": "projects/p/locations/eu/jobs/other-name",  # explicit resource
            }}),
        )

    Thread-safe: the Google clients are safe to share, and the id map is locked.
    """

    def __init__(
        self,
        *,
        project: str | None = None,
        location: str | None = None,
        jobs_client: Any = None,
        executions_client: Any = None,
        logging_client: Any = None,
        name: str = "cloudrun",
        tracked_ids: int = 10_000,
    ) -> None:
        if not project or not location:
            raise ConfigurationError(
                "CloudRunJobBackend needs both 'project' and 'location' "
                "(e.g. project='my-project', location='europe-west1')"
            )
        self._project = project
        self._location = location
        self._jobs_client = jobs_client
        self._executions_client = executions_client
        self._logging_client = logging_client
        self._name = name
        # Cloud Run names executions itself, so remember which of its names goes
        # with which Taskferry id; a full resource path is accepted directly.
        self._ids = ExternalIdIndex(capacity=tracked_ids, recognises=has_prefix("projects/"))

    @property
    def name(self) -> str:
        return self._name

    @property
    def kind(self) -> ExecutionKind:
        return ExecutionKind.JOB

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(CLOUD_RUN_CAPABILITIES, provider=self._name)

    @property
    def project(self) -> str:
        return self._project

    @property
    def location(self) -> str:
        return self._location

    # -- clients (lazy, injectable) --------------------------------------------- #
    def _jobs(self) -> Any:
        if self._jobs_client is None:
            self._jobs_client = _run_v2().JobsClient()
        return self._jobs_client

    def _executions(self) -> Any:
        if self._executions_client is None:
            self._executions_client = _run_v2().ExecutionsClient()
        return self._executions_client

    def _logging(self) -> Any:
        if self._logging_client is None:
            self._logging_client = _logging_v2().Client(project=self._project)
        return self._logging_client

    # -- resource names ----------------------------------------------------------- #
    def job_resource(self, spec: JobSpec) -> str:
        """Fully-qualified Cloud Run Job resource this spec targets."""
        override = spec.options_for("cloudrun").get("job")
        if isinstance(override, str) and override:
            return override
        return f"projects/{self._project}/locations/{self._location}/jobs/{spec.job}"

    # -- submission ---------------------------------------------------------------- #
    def _submit(self, spec: ExecutionSpec) -> Execution:
        assert isinstance(spec, JobSpec)
        request: dict[str, Any] = {"name": self.job_resource(spec)}
        overrides = self._overrides(spec)
        if overrides:
            request["overrides"] = overrides

        try:
            operation = self._jobs().run_job(request=request)
        except Exception as exc:
            raise SubmissionError(
                f"Cloud Run could not start job {spec.job!r} in {self._location}: {exc}",
                backend=self._name,
            ) from exc

        external_id = _execution_name(operation)
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
                provider="gcp",
                provider_id=external_id,
                region=self._location,
                resource=self.job_resource(spec),
                labels=dict(spec.labels),
            ),
            metadata={"profile": spec.profile},
        )

    def _overrides(self, spec: JobSpec) -> dict[str, Any]:
        """Translate the portable parts of a JobSpec into Cloud Run overrides."""
        container: dict[str, Any] = {}
        if spec.argv:
            container["args"] = list(spec.argv)
        if spec.env:
            container["env"] = [{"name": key, "value": value} for key, value in spec.env.items()]

        overrides: dict[str, Any] = {}
        if container:
            overrides["container_overrides"] = [container]
        if spec.parallelism > 1:
            overrides["task_count"] = spec.parallelism
        if spec.timeout.seconds is not None:
            overrides["timeout"] = {"seconds": int(spec.timeout.seconds)}
        if spec.retry.enabled:
            # Cloud Run counts *re*-tries, RetryPolicy counts total attempts.
            overrides["task_count"] = overrides.get("task_count", spec.parallelism)
            overrides["max_retries"] = spec.retry.engine_attempts - 1
        return overrides

    # -- observation ------------------------------------------------------------------ #
    def _get(self, execution_id: ExecutionId) -> Execution:
        external_id = self._ids.resolve(str(execution_id))
        if external_id is None:
            raise ExecutionNotFound(
                f"{execution_id!r} was not submitted by this backend instance; pass the "
                "Cloud Run execution name (projects/.../executions/...) to look it up "
                "from another process",
                backend=self._name,
            )
        try:
            remote = self._executions().get_execution(name=external_id)
        except Exception as exc:
            if _is_not_found(exc):
                raise ExecutionNotFound(
                    f"Cloud Run has no execution {external_id!r}", backend=self._name
                ) from exc
            raise BackendError(
                f"Cloud Run could not read execution {external_id!r}: {exc}", backend=self._name
            ) from exc

        state, error = map_execution_state(remote)
        return Execution(
            id=execution_id,
            kind=ExecutionKind.JOB,
            backend=self._name,
            state=state,
            name=str(getattr(remote, "job", "") or ""),
            started_at=_as_datetime(getattr(remote, "start_time", None)),
            finished_at=_as_datetime(getattr(remote, "completion_time", None)),
            external_id=external_id,
            result=ExecutionResult(error=error, logs_uri=self.logs_uri(external_id))
            if state.is_terminal
            else None,
            provider_metadata=ProviderMetadata(
                provider="gcp",
                provider_id=external_id,
                region=self._location,
                resource=str(getattr(remote, "job", "") or ""),
            ),
            metadata={"logs_uri": self.logs_uri(external_id)},
        )

    def _cancel(self, execution_id: ExecutionId) -> Execution:
        external_id = self._ids.resolve(str(execution_id))
        if external_id is None:
            raise ExecutionNotFound(
                f"{execution_id!r} was not submitted by this backend instance", backend=self._name
            )
        try:
            self._executions().cancel_execution(name=external_id)
        except Exception as exc:
            raise BackendError(
                f"Cloud Run could not cancel execution {external_id!r}: {exc}", backend=self._name
            ) from exc
        return self._get(execution_id)

    def logs_uri(self, external_id: str) -> str:
        """A Cloud Logging console link for an execution. No request is made."""
        return (
            "https://console.cloud.google.com/logs/query"
            f"?project={self._project}&query=resource.labels.location%3D%22{self._location}%22"
        )

    # -- logs ----------------------------------------------------------------------- #
    def logs(self, execution_id: ExecutionId) -> str:
        """Read the execution's Cloud Logging output once. Requires ``Capability.LOGS``."""
        self.capabilities.require(Capability.LOGS)
        return "\n".join(self.stream_logs(execution_id, follow=False))

    def stream_logs(
        self,
        execution_id: ExecutionId,
        *,
        follow: bool = True,
        poll_interval: float = 2.0,
        timeout: float | None = None,
    ) -> Iterator[str]:
        """Yield the execution's Cloud Logging entries, following them live by default.

        Cloud Logging is queried by filter rather than truly streamed, so this
        pages entries in timestamp order, de-duplicating by insert id, and — with
        ``follow=True`` — keeps polling for new entries until the execution reaches
        a terminal state. With ``follow=False`` it returns what is there and stops.
        ``timeout`` bounds the total stream. Requires ``Capability.LOGS``.
        """
        self.capabilities.require(Capability.LOGS)
        external_id = self._ids.resolve(str(execution_id))
        if external_id is None:
            raise ExecutionNotFound(
                f"{execution_id!r} was not submitted by this backend instance; pass the "
                "Cloud Run execution name to stream its logs from another process",
                backend=self._name,
            )
        execution_name = external_id.rsplit("/executions/", 1)[-1]
        base_filter = (
            'resource.type="cloud_run_job" '
            f'AND resource.labels.location="{self._location}" '
            f'AND labels."run.googleapis.com/execution_name"="{execution_name}"'
        )
        deadline = None if timeout is None else time.monotonic() + timeout
        client = self._logging()
        seen: set[str] = set()
        since: str | None = None
        while True:
            log_filter = base_filter if since is None else f'{base_filter} AND timestamp>="{since}"'
            try:
                entries = list(
                    client.list_entries(
                        resource_names=[f"projects/{self._project}"],
                        filter_=log_filter,
                        order_by="timestamp asc",
                        page_size=1000,
                    )
                )
            except Exception as exc:
                if _is_not_found(exc):
                    return
                raise BackendError(
                    f"Cloud Run could not read logs for {external_id!r}: {exc}", backend=self._name
                ) from exc
            new = 0
            for entry in entries:
                insert_id = str(getattr(entry, "insert_id", "") or "")
                if insert_id and insert_id in seen:
                    continue
                if insert_id:
                    seen.add(insert_id)
                timestamp = getattr(entry, "timestamp", None)
                if timestamp is not None:
                    since = _rfc3339(timestamp)
                text = _entry_text(entry)
                if text is not None:
                    yield text
                    new += 1
            if new == 0:
                if not follow or self._is_terminal(external_id):
                    return
                if deadline is not None and time.monotonic() > deadline:
                    return
                time.sleep(poll_interval)

    def _is_terminal(self, external_id: str) -> bool:
        try:
            remote = self._executions().get_execution(name=external_id)
        except Exception:  # a vanished execution is, for our purposes, done
            return True
        return map_execution_state(remote)[0].is_terminal


def _run_v2() -> Any:
    """Import ``google.cloud.run_v2`` lazily, with an actionable error."""
    try:
        from google.cloud import run_v2
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ConfigurationError(
            "the Cloud Run backend needs the Google SDK: pip install 'taskferry-cloudrun[gcp]'"
        ) from exc
    return run_v2


def _logging_v2() -> Any:
    """Import ``google.cloud.logging_v2`` lazily, with an actionable error."""
    try:
        from google.cloud import logging_v2
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ConfigurationError(
            "the Cloud Run backend needs the Google logging SDK: "
            "pip install 'taskferry-cloudrun[gcp]'"
        ) from exc
    return logging_v2


def _entry_text(entry: Any) -> str | None:
    """Extract a line of text from a Cloud Logging entry (text or struct payload)."""
    payload = getattr(entry, "payload", None)
    if payload is None:
        return None
    if isinstance(payload, str):
        return payload.rstrip("\n")
    if isinstance(payload, dict):
        message = payload.get("message")
        return str(message if message is not None else payload).rstrip("\n")
    return str(payload).rstrip("\n")


def _rfc3339(timestamp: Any) -> str:
    """Format a timestamp for a Cloud Logging ``timestamp>=`` filter."""
    if isinstance(timestamp, datetime):
        moment = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
        return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return str(timestamp)


def _execution_name(operation: Any) -> str | None:
    """Pull the execution resource name out of the long-running operation."""
    metadata = getattr(operation, "metadata", None)
    name = getattr(metadata, "name", None)
    if isinstance(name, str) and name:
        return name
    fallback = getattr(operation, "name", None)
    return fallback if isinstance(fallback, str) and fallback else None


def _as_datetime(value: Any) -> datetime | None:
    """Normalise a protobuf timestamp (or a real datetime) to an aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    converter = getattr(value, "ToDatetime", None)
    if callable(converter):
        converted = converter()
        if not isinstance(converted, datetime):  # pragma: no cover - hostile fake
            return None
        return converted.replace(tzinfo=UTC) if converted.tzinfo is None else converted
    return None


def _is_not_found(exc: Exception) -> bool:
    """Whether a Google API error means "no such resource".

    Matched by class name rather than by importing ``google.api_core``, so this
    check works even when only a fake client is installed.
    """
    if type(exc).__name__ == "NotFound":
        return True
    return getattr(exc, "code", None) == 404


def make_backend(**options: Any) -> CloudRunJobBackend:
    """Entry point for ``{"factory": "cloudrun", ...}`` configuration."""
    return CloudRunJobBackend(
        project=options.get("project"),
        location=options.get("location"),
        jobs_client=options.get("jobs_client"),
        executions_client=options.get("executions_client"),
        logging_client=options.get("logging_client"),
        name=str(options.get("name", "cloudrun")),
        tracked_ids=int(options.get("tracked_ids", 10_000)),
    )


__all__ = [
    "CLOUD_RUN_CAPABILITIES",
    "CloudRunJobBackend",
    "make_backend",
    "map_execution_state",
]
