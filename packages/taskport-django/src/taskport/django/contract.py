"""Reusable contract test suite for Taskport Django Tasks backends (section 37).

Subclass in a test module, pointing at a backend alias configured in the
``TASKS`` setting (wired with a fake provider client) plus a sample module-level
task::

    from taskport.django.contract import TaskBackendContract

    class TestCloudTasks(TaskBackendContract):
        backend_alias = "cloudtasks"

        def sample_task(self):
            return my_module.my_task

Assertions that are mandatory are driven by the backend's advertised capabilities
(ADR-0005). Requires ``pytest`` and a configured Django environment.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Any

import pytest

from django.tasks import task_backends
from django.tasks.base import Task, TaskResult, TaskResultStatus
from django.utils import timezone
from taskport.core import CapabilitySet

from .capabilities import TaskCapability


class TaskBackendContract(ABC):
    """Subclass, set ``backend_alias``, and implement ``sample_task``."""

    #: An alias present in the ``TASKS`` setting pointing at the backend to test.
    backend_alias: str

    @abstractmethod
    def sample_task(self) -> Task:
        """Return a module-level task valid for this backend."""

    def backend(self) -> Any:
        return task_backends[self.backend_alias]

    # -- capability honesty ------------------------------------------------- #
    def test_capabilities_are_a_set_for_provider(self) -> None:
        backend = self.backend()
        caps = backend.taskport_capabilities
        assert isinstance(caps, CapabilitySet)
        assert caps.provider == backend.provider

    def test_capabilities_reflect_django_flags(self) -> None:
        backend = self.backend()
        caps = backend.taskport_capabilities
        assert (TaskCapability.DELAY in caps) == backend.supports_defer
        assert (TaskCapability.PRIORITY in caps) == backend.supports_priority
        assert (TaskCapability.RESULT_TRACKING in caps) == backend.supports_get_result
        assert (TaskCapability.ASYNC_ENQUEUE in caps) == backend.supports_async_task

    # -- enqueue behaviour -------------------------------------------------- #
    def test_enqueue_returns_ready_result(self) -> None:
        backend = self.backend()
        result = backend.enqueue(self.sample_task(), [], {})
        assert isinstance(result, TaskResult)
        assert result.status == TaskResultStatus.READY
        assert result.id
        assert result.backend == backend.alias

    def test_defer_matches_capability(self) -> None:
        backend = self.backend()
        if not backend.supports_defer:  # pragma: no cover - all shipped backends defer
            pytest.skip("backend does not support defer")
        # Bound to this backend's alias so construction-time validation passes.
        deferred = self.sample_task().using(
            backend=self.backend_alias,
            run_after=timezone.now() + timedelta(seconds=60),
        )
        backend.enqueue(deferred, [], {})  # must not raise


__all__ = ["TaskBackendContract"]
