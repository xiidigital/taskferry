"""TaskportTaskBackend — the base every portable Django Tasks backend extends.

It stays a real ``django.tasks`` backend (implements the official
``BaseTaskBackend`` contract, works behind ``from django.tasks import task`` and
the ``TASKS`` setting) while adding the Taskport cross-cutting layer: an honest
capability set, correlation propagation, JSON message building and consistent
``TaskResult`` construction.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from django.tasks.backends.base import BaseTaskBackend
from django.tasks.base import Task, TaskResult, TaskResultStatus
from django.utils import timezone
from django.utils.crypto import get_random_string
from taskport.core import CapabilitySet, Correlation, ensure_correlation

from .capabilities import TaskCapability
from .message import TaskMessage, build_message


class TaskportCapabilityMixin:
    """Adds an honest Taskport capability set to any ``BaseTaskBackend``.

    Reused both by :class:`TaskportTaskBackend` and by the local backend, which
    extends Django's ``ImmediateBackend`` directly.
    """

    # These attributes are provided by the concrete BaseTaskBackend subclass.
    supports_priority: bool
    supports_defer: bool
    supports_get_result: bool
    supports_async_task: bool
    queues: set[str]

    #: Provider key used in metadata/spans (e.g. "gcp", "aws", "local").
    provider: str = "unknown"

    #: Provider-specific capabilities beyond those implied by the Django flags.
    taskport_extra_capabilities: frozenset[TaskCapability] = frozenset()

    @property
    def taskport_capabilities(self) -> CapabilitySet:
        """Honest capability set derived from Django flags + provider extras."""
        caps: set[TaskCapability] = set(self.taskport_extra_capabilities)
        if self.supports_priority:
            caps.add(TaskCapability.PRIORITY)
        if self.supports_defer:
            caps.add(TaskCapability.DELAY)
            caps.add(TaskCapability.SCHEDULED_EXECUTION)
        if self.supports_get_result:
            caps.add(TaskCapability.RESULT_TRACKING)
        if self.supports_async_task:
            caps.add(TaskCapability.ASYNC_ENQUEUE)
        if self.queues:
            caps.add(TaskCapability.QUEUE_SELECTION)
        return CapabilitySet(caps, provider=self.provider)


class TaskportTaskBackend(TaskportCapabilityMixin, BaseTaskBackend):
    """Abstract base for Taskport Django Tasks backends that ship a message.

    Subclasses set the Django feature flags (``supports_defer`` etc.), optionally
    declare ``taskport_extra_capabilities``, and implement ``enqueue``.
    """

    @abstractmethod
    def enqueue(self, task: Task, args: list[Any], kwargs: dict[str, Any]) -> TaskResult: ...

    # -- helpers for subclasses -------------------------------------------- #
    def _correlation(self) -> Correlation:
        """Correlation for the current flow (starts fresh if none is bound)."""
        return ensure_correlation()

    def _build_message(self, task: Task, args: list[Any], kwargs: dict[str, Any]) -> TaskMessage:
        return build_message(task, tuple(args), dict(kwargs), correlation=self._correlation())

    def _make_result(
        self,
        task: Task,
        args: list[Any],
        kwargs: dict[str, Any],
        *,
        result_id: str | None = None,
        status: TaskResultStatus = TaskResultStatus.READY,
    ) -> TaskResult:
        return TaskResult(
            task=task,
            id=result_id or get_random_string(32),
            status=status,
            enqueued_at=timezone.now(),
            started_at=None,
            finished_at=None,
            last_attempted_at=None,
            args=list(args),
            kwargs=dict(kwargs),
            backend=self.alias,
            errors=[],
            worker_ids=[],
        )


__all__ = ["TaskportCapabilityMixin", "TaskportTaskBackend"]
