"""SQSBackend — send tasks to an AWS SQS queue.

SQS is pull-based: a consumer (Lambda, ECS/Fargate) reads the queue and executes
the task via :func:`taskport.django.consumers.process_sqs_message`. This backend
handles the enqueue (send) side.

SQS caps at a 15-minute (900s) delivery delay, so ``run_after`` further out is
rejected loudly rather than silently clamped (honest capabilities, ADR-0005).
FIFO queues require a message group and are advertised with ``ORDERING``.
The boto3 client is imported lazily and can be injected for testing (ADR-0007).
"""

from __future__ import annotations

from typing import Any

from django.tasks.base import Task, TaskResult
from django.tasks.exceptions import InvalidTask
from django.utils import timezone
from taskport.core import (
    ATTR_PROVIDER,
    SPAN_TASK_ENQUEUE,
    ConfigurationError,
    JsonSerializer,
    ProviderError,
    span,
)

from ..base import TaskportTaskBackend
from ..capabilities import TaskCapability

_SERIALIZER = JsonSerializer()
_MAX_DELAY_SECONDS = 900


class SQSBackend(TaskportTaskBackend):
    """Enqueues tasks onto an AWS SQS queue."""

    provider = "aws"

    supports_defer = True
    supports_async_task = True
    supports_get_result = False
    supports_priority = False

    def __init__(self, alias: str, params: dict[str, Any]) -> None:
        super().__init__(alias, params)
        self._client = self.options.get("client")
        self._queue_url = str(self.options.get("queue_url", ""))
        self._is_fifo = self._queue_url.endswith(".fifo")
        extra = {TaskCapability.DEAD_LETTER}
        if self._is_fifo:
            extra.add(TaskCapability.ORDERING)
        self.taskport_extra_capabilities = frozenset(extra)

    def _sqs(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover
                raise ProviderError(
                    "boto3 is required for SQSBackend; install taskport-django[aws]",
                    provider="aws",
                ) from exc
            self._client = boto3.client("sqs", region_name=self.options.get("region"))
        return self._client

    def _delay_seconds(self, task: Task) -> int:
        if task.run_after is None:
            return 0
        delay = int((task.run_after - timezone.now()).total_seconds())
        if delay < 0:
            return 0
        if delay > _MAX_DELAY_SECONDS:
            raise InvalidTask(
                f"SQS supports a maximum delay of {_MAX_DELAY_SECONDS}s; "
                f"requested {delay}s. Use a scheduler for longer delays."
            )
        return delay

    def enqueue(self, task: Task, args: list[Any], kwargs: dict[str, Any]) -> TaskResult:
        self.validate_task(task)
        if not self._queue_url:
            raise ConfigurationError("SQSBackend requires OPTIONS['queue_url']")
        message = self._build_message(task, args, kwargs)
        request: dict[str, Any] = {
            "QueueUrl": self._queue_url,
            "MessageBody": _SERIALIZER.dumps(message).decode("utf-8"),  # type: ignore[arg-type]
            "DelaySeconds": self._delay_seconds(task),
        }
        if self._is_fifo:
            group = self.options.get("message_group_id", task.queue_name)
            request["MessageGroupId"] = str(group)
            # DelaySeconds is not allowed on FIFO queues.
            request.pop("DelaySeconds", None)

        with span(SPAN_TASK_ENQUEUE, {ATTR_PROVIDER: self.provider}):
            try:
                response = self._sqs().send_message(**request)
            except Exception as exc:
                raise ProviderError(f"send_message failed: {exc}", provider="aws") from exc
        message_id = response.get("MessageId")
        return self._make_result(task, args, kwargs, result_id=message_id)


__all__ = ["SQSBackend"]
