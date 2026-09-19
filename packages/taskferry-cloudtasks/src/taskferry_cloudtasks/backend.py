"""The Cloud Tasks `TaskBackend`.

Capabilities, and the two that are missing
------------------------------------------

Advertised: ``SUBMIT``, ``DELAY`` (``schedule_time``), ``RETRY`` (the queue's own
retry configuration), ``DEDUPLICATION`` (a task *name* is unique per queue, which
is real deduplication with a documented window).

Not advertised: ``STATE`` and ``RESULT``. Cloud Tasks is fire-and-forget — once a
task is created there is no per-task status to read and no place a return value
is kept. A handle from this backend therefore raises
:class:`~taskferry.errors.UnsupportedCapability` on ``status()`` rather than
returning a plausible-looking ``UNKNOWN`` forever, and an application that needs
to know whether the work happened records that itself, in its own database, where
it is actually true.

That asymmetry with Procrastinate is not a defect in the abstraction; it is the
abstraction working. Both engines run the same task code, and the difference in
what you can *ask* afterwards is visible, checkable and impossible to trip over
by accident.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from taskferry.capabilities import Capability, CapabilitySet
from taskferry.core.correlation import Correlation
from taskferry.core.provider import ProviderMetadata
from taskferry.envelope import ENVELOPE_VERSION, Envelope, build_envelope
from taskferry.errors import ConfigurationError, SubmissionError
from taskferry.execution import (
    Execution,
    ExecutionKind,
    ExecutionState,
    new_execution_id,
)
from taskferry.ports import BaseBackend
from taskferry.specs import ExecutionSpec, TaskSpec

MESSAGE_VERSION = ENVELOPE_VERSION
"""Alias of the shared envelope version; the format is the core's, not ours."""

CLOUD_TASKS_CAPABILITIES = frozenset(
    {
        Capability.SUBMIT,
        Capability.DELAY,
        Capability.RETRY,
        Capability.DEDUPLICATION,
    }
)


CloudTasksMessage = Envelope
"""The JSON body Cloud Tasks POSTs to your service — the shared envelope."""

build_message = build_envelope
"""Serialize a spec into the HTTP body the receiver will decode."""


class CloudTasksBackend(BaseBackend):
    """Creates HTTP-target tasks on a Cloud Tasks queue.

    Args:
        project: GCP project id.
        location: Queue region.
        queue: Default queue name. ``TaskSpec.queue`` overrides it, which is what
            makes ``queue="email"`` mean a real Cloud Tasks queue.
        url: The endpoint Cloud Tasks will POST to. Your service routes it to
            :func:`taskferry_cloudtasks.handle_request`.
        service_account_email: Identity for the OIDC token, so the endpoint can
            require authentication instead of being open to the internet.
        audience: OIDC audience, when it differs from ``url``.
        client: Injected ``tasks_v2.CloudTasksClient`` for testing.

    Backend options, under the ``"cloudtasks"`` namespace: ``dispatch_deadline``,
    ``headers``, and ``queue`` for a one-off override.
    """

    def __init__(
        self,
        *,
        project: str | None = None,
        location: str | None = None,
        queue: str = "default",
        url: str | None = None,
        service_account_email: str | None = None,
        audience: str | None = None,
        client: Any = None,
        async_client: Any = None,
        name: str = "cloudtasks",
    ) -> None:
        missing = [
            key
            for key, value in (("project", project), ("location", location), ("url", url))
            if not value
        ]
        if missing:
            raise ConfigurationError(
                f"CloudTasksBackend needs {', '.join(missing)}; 'url' is the endpoint in your "
                "service that Cloud Tasks will POST each task to"
            )
        assert project and location and url  # narrowed by the check above
        self._project = project
        self._location = location
        self._queue = queue
        self._url = url
        self._service_account_email = service_account_email
        self._audience = audience
        self._client = client
        self._async_client = async_client
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def kind(self) -> ExecutionKind:
        return ExecutionKind.TASK

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(CLOUD_TASKS_CAPABILITIES, provider=self._name)

    def _tasks(self) -> Any:
        if self._client is None:
            try:
                from google.cloud import tasks_v2
            except ImportError as exc:  # pragma: no cover - depends on the environment
                raise ConfigurationError(
                    "the Cloud Tasks backend needs the Google SDK: "
                    "pip install 'taskferry-cloudtasks[gcp]'"
                ) from exc
            self._client = tasks_v2.CloudTasksClient()
        return self._client

    def queue_path(self, queue: str) -> str:
        client = self._tasks()
        builder = getattr(client, "queue_path", None)
        if callable(builder):
            return str(builder(self._project, self._location, queue))
        return f"projects/{self._project}/locations/{self._location}/queues/{queue}"

    def _queue_path_str(self, queue: str) -> str:
        """Fully-qualified queue path without needing a client (async path)."""
        return f"projects/{self._project}/locations/{self._location}/queues/{queue}"

    # -- submission -------------------------------------------------------------- #
    def _queue_for(self, spec: TaskSpec) -> str:
        options = spec.options_for("cloudtasks")
        return str(options.get("queue") or spec.queue or self._queue)

    def _build_task(self, spec: TaskSpec, queue_path: str) -> dict[str, Any]:
        """The Cloud Tasks task body. Pure, shared by the sync and async paths."""
        options = spec.options_for("cloudtasks")
        http_request: dict[str, Any] = {
            "http_method": "POST",
            "url": self._url,
            "headers": {
                "Content-Type": "application/json",
                **self._correlation_headers(spec.correlation),
                **dict(options.get("headers") or {}),  # type: ignore[arg-type]
            },
            "body": json.dumps(build_message(spec)).encode("utf-8"),
        }
        if self._service_account_email:
            oidc: dict[str, str] = {"service_account_email": self._service_account_email}
            if self._audience or self._url:
                oidc["audience"] = self._audience or self._url
            http_request["oidc_token"] = oidc

        task: dict[str, Any] = {"http_request": http_request}
        scheduled_for = spec.scheduled_for()
        if scheduled_for is not None:
            task["schedule_time"] = scheduled_for
        if "dispatch_deadline" in options:
            task["dispatch_deadline"] = options["dispatch_deadline"]
        if spec.idempotency_key is not None:
            # A named task is refused if the name was used recently — Cloud Tasks'
            # own, real, time-bounded deduplication. The window is Google's, not
            # ours, and this is not an exactly-once promise.
            task["name"] = f"{queue_path}/tasks/{_safe_name(spec.idempotency_key)}"
        return task

    def _execution_from(
        self, spec: TaskSpec, created: Any, queue_path: str, queue: str
    ) -> Execution:
        external_id = getattr(created, "name", None)
        return Execution(
            id=new_execution_id(ExecutionKind.TASK),
            kind=ExecutionKind.TASK,
            backend=self._name,
            # QUEUED is the last thing this backend can honestly observe: Cloud
            # Tasks will not tell us anything after creation.
            state=ExecutionState.QUEUED,
            name=spec.name,
            created_at=datetime.now(UTC),
            external_id=str(external_id) if external_id else None,
            correlation=spec.correlation,
            provider_metadata=ProviderMetadata(
                provider="gcp",
                provider_id=str(external_id) if external_id else None,
                region=self._location,
                resource=queue_path,
                labels=dict(spec.labels),
            ),
            metadata={"queue": queue, "url": self._url},
        )

    def _submit(self, spec: ExecutionSpec) -> Execution:
        assert isinstance(spec, TaskSpec)
        queue = self._queue_for(spec)
        queue_path = self.queue_path(queue)
        task = self._build_task(spec, queue_path)
        try:
            created = self._tasks().create_task(request={"parent": queue_path, "task": task})
        except Exception as exc:
            raise SubmissionError(
                f"Cloud Tasks could not create a task for {spec.task!r} on queue {queue!r}: {exc}",
                backend=self._name,
            ) from exc
        return self._execution_from(spec, created, queue_path, queue)

    # -- native async (CloudTasksAsyncClient) ------------------------------------- #
    @asynccontextmanager
    async def _async_tasks(self) -> AsyncIterator[Any]:
        if self._async_client is not None:
            yield self._async_client
            return
        client = _async_tasks_client()
        try:
            yield client
        finally:  # pragma: no cover - real gRPC client only
            close = getattr(getattr(client, "transport", None), "close", None)
            if callable(close):
                await close()

    async def asubmit(self, spec: ExecutionSpec) -> Execution:
        """Native async submit via ``CloudTasksAsyncClient``, else the thread path."""
        if self._async_client is None and not _has_cloud_tasks():
            return await super().asubmit(spec)
        assert isinstance(spec, TaskSpec)
        self.validate(spec)
        self.hooks.before_submit(spec, self.name)
        queue = self._queue_for(spec)
        queue_path = self._queue_path_str(queue)
        task = self._build_task(spec, queue_path)
        try:
            async with self._async_tasks() as client:
                created = await client.create_task(request={"parent": queue_path, "task": task})
            execution = self._execution_from(spec, created, queue_path, queue)
        except Exception as exc:
            error = SubmissionError(
                f"Cloud Tasks could not create a task for {spec.task!r} on queue {queue!r}: {exc}",
                backend=self._name,
            )
            self.hooks.on_submit_error(spec, self.name, error)
            raise error from exc
        self.hooks.after_submit(spec, execution)
        return execution

    @staticmethod
    def _correlation_headers(correlation: Correlation | None) -> dict[str, str]:
        """Propagate correlation and trace context as HTTP headers.

        This is how a trace survives the hop through Google's infrastructure: the
        receiver rebuilds the correlation from these headers, so one flow stays
        followable from the web request through the queue into the task.
        """
        return correlation.to_headers() if correlation is not None else {}


def _safe_name(key: str) -> str:
    """Reduce an idempotency key to the characters Cloud Tasks allows in a name."""
    cleaned = "".join(char if char.isalnum() or char in "-_" else "-" for char in key)
    return cleaned[:500] or "taskferry"


def _has_cloud_tasks() -> bool:
    """Whether the Google Cloud Tasks SDK is importable, without importing it."""
    from importlib.util import find_spec

    try:
        # A dotted name raises (not returns None) when a parent package like
        # ``google`` is absent, so both outcomes mean "not installed".
        return find_spec("google.cloud.tasks_v2") is not None
    except ModuleNotFoundError:
        return False


def _async_tasks_client() -> Any:
    """A lazily-built ``CloudTasksAsyncClient``, with an actionable error."""
    try:
        from google.cloud import tasks_v2
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ConfigurationError(
            "the Cloud Tasks backend needs the Google SDK: pip install 'taskferry-cloudtasks[gcp]'"
        ) from exc
    return tasks_v2.CloudTasksAsyncClient()


def make_backend(**options: Any) -> CloudTasksBackend:
    """Entry point for ``{"factory": "cloudtasks", ...}`` configuration."""
    return CloudTasksBackend(
        project=options.get("project"),
        location=options.get("location"),
        queue=str(options.get("queue", "default")),
        url=options.get("url"),
        service_account_email=options.get("service_account_email"),
        audience=options.get("audience"),
        client=options.get("client"),
        async_client=options.get("async_client"),
        name=str(options.get("name", "cloudtasks")),
    )


__all__ = [
    "CLOUD_TASKS_CAPABILITIES",
    "MESSAGE_VERSION",
    "CloudTasksBackend",
    "CloudTasksMessage",
    "build_message",
    "make_backend",
]
