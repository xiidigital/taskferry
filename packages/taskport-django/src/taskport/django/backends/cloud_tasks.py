"""CloudTasksBackend — serverless, push-based Django Tasks backend (GCP).

Cloud Tasks pushes an HTTP request to your service (e.g. Cloud Run) which then
executes the task — no standing worker, scale-to-zero (section 44). This backend
handles the **enqueue** (send) side; the receive side is
:func:`taskport.django.views.task_webhook`.

The Google SDK is imported lazily and the client can be injected for testing
(ADR-0007). Configure via the official ``TASKS`` setting::

    TASKS = {
        "default": {
            "BACKEND": "taskport.django.backends.cloud_tasks.CloudTasksBackend",
            "OPTIONS": {
                "project": "my-project",
                "location": "us-central1",
                "queue": "default",
                "url": "https://my-service.run.app/_taskport/execute",
                "service_account_email": "runner@my-project.iam.gserviceaccount.com",
            },
        }
    }
"""

from __future__ import annotations

from typing import Any

from django.tasks.base import Task, TaskResult
from taskport.core import (
    ATTR_PROVIDER,
    ATTR_TASK_ID,
    SPAN_TASK_ENQUEUE,
    ConfigurationError,
    JsonSerializer,
    ProviderError,
    span,
)

from ..base import TaskportTaskBackend
from ..capabilities import TaskCapability

_SERIALIZER = JsonSerializer()


class CloudTasksBackend(TaskportTaskBackend):
    """Enqueues tasks onto a Google Cloud Tasks queue (HTTP push targets)."""

    provider = "gcp"

    supports_defer = True
    supports_async_task = True
    supports_get_result = False
    supports_priority = False

    taskport_extra_capabilities = frozenset(
        {TaskCapability.SCHEDULED_EXECUTION, TaskCapability.RETRIES}
    )

    def __init__(self, alias: str, params: dict[str, Any]) -> None:
        super().__init__(alias, params)
        self._client = self.options.get("client")

    def _require(self, key: str) -> str:
        value = self.options.get(key)
        if not value:
            raise ConfigurationError(f"CloudTasksBackend requires OPTIONS[{key!r}]")
        return str(value)

    def _tasks_client(self) -> Any:
        if self._client is None:
            try:
                from google.cloud import tasks_v2
            except ImportError as exc:  # pragma: no cover
                raise ProviderError(
                    "google-cloud-tasks is required for CloudTasksBackend; "
                    "install taskport-django[gcp]",
                    provider="gcp",
                ) from exc
            self._client = tasks_v2.CloudTasksClient()
        return self._client

    def enqueue(self, task: Task, args: list[Any], kwargs: dict[str, Any]) -> TaskResult:
        self.validate_task(task)
        client = self._tasks_client()
        parent = client.queue_path(
            self._require("project"), self._require("location"), self._require("queue")
        )
        message = self._build_message(task, args, kwargs)
        http_request: dict[str, Any] = {
            "http_method": "POST",
            "url": self._require("url"),
            "headers": {"Content-Type": "application/json"},
            "body": _SERIALIZER.dumps(message),  # type: ignore[arg-type]
        }
        service_account = self.options.get("service_account_email")
        if service_account:
            oidc: dict[str, str] = {"service_account_email": str(service_account)}
            audience = self.options.get("audience")
            if audience:
                oidc["audience"] = str(audience)
            http_request["oidc_token"] = oidc

        cloud_task: dict[str, Any] = {"http_request": http_request}
        if task.run_after is not None:
            cloud_task["schedule_time"] = task.run_after

        with span(SPAN_TASK_ENQUEUE, {ATTR_PROVIDER: self.provider}) as s:
            try:
                created = client.create_task(request={"parent": parent, "task": cloud_task})
            except Exception as exc:
                raise ProviderError(f"create_task failed: {exc}", provider="gcp") from exc
            # The Cloud Tasks task name (projects/.../tasks/<id>) can exceed the
            # 64-char TaskResult.id limit, so it is recorded on the span rather
            # than used as the result id (get_result is unsupported anyway).
            provider_name = getattr(created, "name", None)
            s.set_attribute(ATTR_TASK_ID, provider_name or "")
        return self._make_result(task, args, kwargs)


__all__ = ["CloudTasksBackend"]
