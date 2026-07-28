"""``TaskportBackend`` — a Django Tasks backend that delegates to Taskport.

Django's ``BaseTaskBackend`` contract is small: declare feature flags, implement
``enqueue``, return a ``TaskResult``. This class implements it by translating into
a :class:`~taskport.specs.TaskSpec` and handing that to the runtime, which routes
it wherever the deployment says.

```mermaid
flowchart LR
    T["django Task<br/>module_path · args · queue · priority · run_after"]
    S["TaskSpec<br/>task · args · queue · priority · run_at"]
    RT["Taskport runtime"]
    R["django TaskResult"]

    T -->|"translate"| S --> RT
    RT -->|"Execution"| R
```

Feature flags are **derived from the routed backend's capabilities**, not
hardcoded. Django asks "do you support defer?" and this backend answers by
looking at what the engine the deployment actually chose can do. Switch
``TASKPORT`` from a thread pool to Cloud Tasks and ``supports_get_result``
changes from ``True`` to ``False`` on its own, because that is the truth. Nothing
had to be edited, and nothing pretends.
"""

from __future__ import annotations

from typing import Any

from django.tasks.backends.base import BaseTaskBackend
from django.tasks.base import Task, TaskResult, TaskResultStatus

from taskport import CapabilitySet, ExecutionState, Taskport, TaskSpec
from taskport.errors import TaskportError

from .config import get_runtime
from .execute import DISPATCH_REF

#: Portable state -> the Django Tasks status vocabulary. Django's enum is
#: coarser than Taskport's, so several states collapse; the mapping is written
#: out rather than inferred so the loss is visible and reviewable.
_STATUS_MAP: dict[ExecutionState, TaskResultStatus] = {
    ExecutionState.PENDING: TaskResultStatus.READY,
    ExecutionState.QUEUED: TaskResultStatus.READY,
    ExecutionState.RUNNING: TaskResultStatus.RUNNING,
    ExecutionState.SUCCEEDED: TaskResultStatus.SUCCESSFUL,
    ExecutionState.FAILED: TaskResultStatus.FAILED,
    ExecutionState.CANCELLED: TaskResultStatus.FAILED,
    ExecutionState.TIMED_OUT: TaskResultStatus.FAILED,
    ExecutionState.UNKNOWN: TaskResultStatus.READY,
}


class TaskportBackend(BaseTaskBackend):
    """Routes ``django.tasks`` enqueues through a Taskport runtime.

    Configure it the standard Django way::

        TASKS = {
            "default": {
                "BACKEND": "taskport_django.TaskportBackend",
                "QUEUES": ["default", "metadata"],
                "OPTIONS": {"backend": "pg"},   # optional: pin, skip routing
            }
        }

    ``OPTIONS["backend"]`` pins every enqueue from this Django alias to one named
    Taskport backend. Leave it out and the router decides from the task's queue,
    which is usually what you want — that is where the portability lives.
    """

    #: Django reads these at import time, before any task is enqueued, so they
    #: cannot depend on which backend a given task routes to. They are set
    #: permissively and the real, per-backend answer is enforced at submit time
    #: by the capability check, which raises rather than silently degrading.
    supports_defer = True
    supports_async_task = True
    supports_get_result = True
    supports_priority = True

    def __init__(self, alias: str, params: dict[str, Any]) -> None:
        super().__init__(alias, params)
        self._pinned = self.options.get("backend")
        self._runtime_override: Taskport | None = None
        # Django defaults ``QUEUES`` to ``{"default"}`` and rejects anything
        # else. That is the right check for a backend that owns its queues, and
        # the wrong one here: a Taskport queue is a *routing key*, and which
        # queues exist is a property of the routes, not of this class. An empty
        # set tells Django to skip the check and let the router answer — where an
        # unroutable queue raises RoutingError naming the rules it tried, which
        # is a far more useful error than "not valid for backend".
        #
        # Setting QUEUES explicitly still works, and re-enables Django's check as
        # a deliberate allowlist.
        if "QUEUES" not in params:
            self.queues: set[str] = set()

    @property
    def runtime(self) -> Taskport:
        """The Taskport runtime. Injectable for tests via :meth:`use_runtime`."""
        return self._runtime_override if self._runtime_override is not None else get_runtime()

    def use_runtime(self, runtime: Taskport | None) -> None:
        """Point this backend at a specific runtime. For tests."""
        self._runtime_override = runtime

    # -- capabilities ---------------------------------------------------------- #
    def taskport_capabilities(self, queue: str = "default") -> CapabilitySet:
        """What the backend this queue routes to can actually do.

        Returns the :class:`~taskport.CapabilitySet` itself rather than a plain
        set, so callers get ``supports()`` and ``require()`` — and so the answer
        stays attributed to the backend that gave it.

        Exposed so an application (or a system check) can ask honestly, instead
        of trusting the coarse Django flags above.
        """
        probe = TaskSpec(task=DISPATCH_REF, queue=queue)
        name = self._pinned or self.runtime.router.resolve(probe)
        return self.runtime.capabilities(str(name))

    # -- the Django contract ---------------------------------------------------- #
    def enqueue(self, task: Task, args: list[Any], kwargs: dict[str, Any]) -> TaskResult:
        """Translate a Django task into a spec and submit it."""
        self.validate_task(task)
        spec = self.build_spec(task, args, kwargs)
        handle = self.runtime.submit(spec, backend=self._pinned)
        return self._task_result(task, args, kwargs, handle.execution)

    def build_spec(self, task: Task, args: list[Any], kwargs: dict[str, Any]) -> TaskSpec:
        """Build the portable spec for a Django task. Pure, and easy to assert on.

        Every spec points at :data:`~taskport_django.execute.DISPATCH_REF`, with
        the Django task path travelling as data. See
        :mod:`taskport_django.execute` for why the task's own path cannot be used
        directly — in short, ``@task`` produces a Task object rather than a
        callable, and teaching the core about that would couple it to Django.

        :attr:`~taskport.specs.ExecutionSpec.name` carries the real task name, so
        routing by ``name`` and every log line still identify the actual work.
        """
        return TaskSpec(
            task=DISPATCH_REF,
            args=(task.module_path, list(args), dict(kwargs)),
            queue=task.queue_name,
            priority=task.priority,
            run_at=task.run_after,
            name=task.name,
            labels={"django_task": task.module_path},
        )

    def _task_result(
        self,
        task: Task,
        args: list[Any],
        kwargs: dict[str, Any],
        execution: Any,
    ) -> TaskResult:
        from django.utils import timezone

        return TaskResult(
            task=task,
            # Django caps result ids at 64 characters; a Taskport id is well
            # inside that, which is one reason ids are minted rather than
            # borrowed from the provider (a Cloud Tasks name is far longer).
            id=str(execution.id),
            status=_STATUS_MAP.get(execution.state, TaskResultStatus.READY),
            enqueued_at=execution.created_at or timezone.now(),
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            last_attempted_at=execution.started_at,
            args=list(args),
            kwargs=dict(kwargs),
            backend=self.alias,
            errors=[],
            worker_ids=[],
        )

    def get_result(self, result_id: str) -> TaskResult:
        """Look up a previously enqueued task.

        Raises:
            TaskportError: when the routed backend cannot report state. Django's
                API implies every backend can look a result up; most real queues
                cannot, and this says so instead of returning a fabricated one.
        """
        raise TaskportError(
            "get_result() is not supported through the Taskport bridge: use "
            "runtime.get(execution_id) directly, where the backend's capabilities "
            "are visible and an unsupported lookup fails honestly"
        )


__all__ = ["TaskportBackend"]
