"""The Celery ``TaskBackend``.

Translation, in both directions, and nothing else. A :class:`~taskferry.specs.TaskSpec`
becomes a Celery task sent to the ``taskferry:dispatch`` task; Celery's task state
is translated back to a portable :class:`~taskferry.execution.ExecutionState`.

```mermaid
flowchart LR
    subgraph Taskferry
        TS["TaskSpec<br/>queue · priority · delay · retry"]
        EX["Execution<br/>ExecutionState"]
    end
    subgraph Celery
        SEND["app.send_task('taskferry:dispatch', ...)"]
        ST["AsyncResult.state<br/>PENDING · STARTED · SUCCESS · FAILURE · RETRY · REVOKED"]
    end
    TS --> SEND
    ST --> EX
```

Capabilities are advertised from what Celery genuinely does out of the box:

* ``DELAY`` — ``eta`` / ``countdown``;
* ``PRIORITY`` — native message priority (broker-dependent, but real);
* ``RETRY`` — the engine owns retries; the dispatcher turns a failure into
  Celery's own ``self.retry`` so the task is genuinely re-queued;
* ``STATE`` — through ``AsyncResult``;
* ``CANCEL`` — ``AsyncResult.revoke``.

``RESULT`` is **not** advertised. Celery *can* store results, but only when a
result backend is configured, and Taskferry will not claim a capability that
depends on optional infrastructure it cannot see. ``DEDUPLICATION`` is not
advertised either: Celery has no native mechanism for it, so a spec carrying an
``idempotency_key`` is rejected here rather than accepted and ignored.

A note on Celery's ``PENDING``: Celery reports ``PENDING`` both for a task that is
genuinely queued *and* for a task id it has never heard of. Taskferry maps
``PENDING`` to ``QUEUED`` and documents that it cannot distinguish the two without
a result backend — an honest limitation, not a guess.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from taskferry.capabilities import Capability, CapabilitySet
from taskferry.core.provider import ProviderMetadata
from taskferry.errors import ConfigurationError, ExecutionNotFound, SubmissionError
from taskferry.execution import (
    Execution,
    ExecutionId,
    ExecutionKind,
    ExecutionState,
    new_execution_id,
)
from taskferry.ports import BaseBackend
from taskferry.specs import ExecutionSpec, TaskSpec
from taskferry.tracking import ExternalIdIndex

from .message import build_message

DEFAULT_DISPATCH_TASK = "taskferry:dispatch"
"""Name of the single Celery task that runs every Taskferry message."""

CELERY_CAPABILITIES = frozenset(
    {
        Capability.SUBMIT,
        Capability.STATE,
        Capability.CANCEL,
        Capability.DELAY,
        Capability.PRIORITY,
        Capability.RETRY,
    }
)

#: Celery task state -> portable state. See the ``PENDING`` note in the module
#: docstring. ``RETRY`` is a task waiting to be re-attempted by the engine, which
#: an operator sees as still running.
_STATE_MAP: dict[str, ExecutionState] = {
    "PENDING": ExecutionState.QUEUED,
    "RECEIVED": ExecutionState.QUEUED,
    "STARTED": ExecutionState.RUNNING,
    "RETRY": ExecutionState.RUNNING,
    "SUCCESS": ExecutionState.SUCCEEDED,
    "FAILURE": ExecutionState.FAILED,
    "REVOKED": ExecutionState.CANCELLED,
}


def map_state(state: object) -> ExecutionState:
    """Map a Celery task state to a portable state. Pure, unit-testable.

    An unrecognised state maps to ``UNKNOWN`` rather than to a guess — a future
    Celery state must not make Taskferry report something false.
    """
    if state is None:
        return ExecutionState.UNKNOWN
    return _STATE_MAP.get(str(state).upper(), ExecutionState.UNKNOWN)


def _looks_like_celery_id(value: str) -> bool:
    """Recognise a Celery task id (a uuid4 string) copied from a dashboard."""
    parts = value.split("-")
    return len(parts) == 5 and all(c in "0123456789abcdef-" for c in value.lower())


class CeleryTaskBackend(BaseBackend):
    """Sends Taskferry tasks to a Celery app.

    Args:
        app: A ``celery.Celery`` instance, or a ``"module:attribute"`` string
            resolved on first use so the backend is constructible before the
            broker connection is configured.
        dispatch_task: Name of the registered dispatcher task. Must match the name
            passed to :func:`~taskferry_celery.register_dispatcher`.
        name: Backend name used in metadata and errors.

    Backend options, under the ``"celery"`` namespace, pass straight to
    ``send_task``::

        TaskSpec(
            task="myapp.tasks:reindex",
            backend_options=BackendOptions({"celery": {"expires": 300}}),
        )

    Thread-safe once constructed: a Celery ``app`` is designed to be shared and
    this backend keeps no mutable state beyond the id index.
    """

    def __init__(
        self,
        *,
        app: Any = None,
        dispatch_task: str = DEFAULT_DISPATCH_TASK,
        name: str = "celery",
        tracked_ids: int = 10_000,
    ) -> None:
        self._app = app
        self._dispatch_task = dispatch_task
        self._name = name
        self._ids = ExternalIdIndex(capacity=tracked_ids, recognises=_looks_like_celery_id)

    @property
    def name(self) -> str:
        return self._name

    @property
    def kind(self) -> ExecutionKind:
        return ExecutionKind.TASK

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(CELERY_CAPABILITIES, provider=self._name)

    # -- the app ---------------------------------------------------------------- #
    def app(self) -> Any:
        """The Celery app, resolving a dotted path on first use."""
        if self._app is None:
            raise ConfigurationError(
                f"{self._name!r} needs a celery app: pass app=<Celery> or "
                "app='myapp.celery:app' in the backend options"
            )
        if isinstance(self._app, str):
            self._app = _import_attribute(self._app)
        return self._app

    # -- submission ------------------------------------------------------------- #
    def _submit(self, spec: ExecutionSpec) -> Execution:
        assert isinstance(spec, TaskSpec)
        app = self.app()
        options: dict[str, Any] = {
            "queue": spec.queue,
            "priority": spec.priority,
        }
        eta = spec.scheduled_for()
        if eta is not None:
            options["eta"] = eta
        # Any celery-namespaced option passes straight through to send_task.
        options.update(spec.options_for("celery"))

        message = build_message(spec)
        try:
            async_result = app.send_task(
                self._dispatch_task, kwargs={"message": message}, **options
            )
        except Exception as exc:
            raise SubmissionError(
                f"celery could not send {spec.task!r} on queue {spec.queue!r}: {exc}",
                backend=self._name,
            ) from exc

        task_id = str(getattr(async_result, "id", "") or "")
        execution_id = new_execution_id(ExecutionKind.TASK)
        self._ids.remember(str(execution_id), task_id)
        return Execution(
            id=execution_id,
            kind=ExecutionKind.TASK,
            backend=self._name,
            state=ExecutionState.QUEUED,
            name=spec.name,
            created_at=datetime.now(UTC),
            external_id=task_id,
            correlation=spec.correlation,
            provider_metadata=ProviderMetadata(
                provider="celery",
                provider_id=task_id,
                resource=f"queue:{spec.queue}",
                labels=dict(spec.labels),
            ),
            metadata={"queue": spec.queue, "celery_task_id": task_id},
        )

    # -- observation ------------------------------------------------------------ #
    def _result_handle(self, execution_id: ExecutionId) -> Any:
        task_id = self._ids.resolve(str(execution_id))
        if task_id is None:
            raise ExecutionNotFound(
                f"celery has no task {execution_id!r}; pass the celery task id when the "
                "execution was submitted by another process",
                backend=self._name,
            )
        return self.app().AsyncResult(task_id)

    def _get(self, execution_id: ExecutionId) -> Execution:
        async_result = self._result_handle(execution_id)
        task_id = str(getattr(async_result, "id", "") or self._ids.resolve(str(execution_id)) or "")
        return Execution(
            id=execution_id,
            kind=ExecutionKind.TASK,
            backend=self._name,
            state=map_state(getattr(async_result, "state", None)),
            external_id=task_id,
            provider_metadata=ProviderMetadata(provider="celery", provider_id=task_id),
        )

    def _cancel(self, execution_id: ExecutionId) -> Execution:
        async_result = self._result_handle(execution_id)
        try:
            async_result.revoke()
        except Exception as exc:
            raise SubmissionError(
                f"celery could not revoke {execution_id!r}: {exc}", backend=self._name
            ) from exc
        return self._get(execution_id)


def _import_attribute(path: str) -> Any:
    import importlib

    module_path, sep, attr = path.partition(":")
    if not sep:
        module_path, _, attr = path.rpartition(".")
    if not module_path or not attr:
        raise ConfigurationError(f"invalid app reference {path!r}; expected 'module:attribute'")
    try:
        return getattr(importlib.import_module(module_path), attr)
    except (ImportError, AttributeError) as exc:
        raise ConfigurationError(f"cannot resolve celery app {path!r}: {exc}") from exc


def make_backend(**options: Any) -> CeleryTaskBackend:
    """Entry point for ``{"factory": "celery", ...}`` configuration."""
    return CeleryTaskBackend(
        app=options.get("app"),
        dispatch_task=str(options.get("dispatch_task", DEFAULT_DISPATCH_TASK)),
        name=str(options.get("name", "celery")),
        tracked_ids=int(options.get("tracked_ids", 10_000)),
    )


__all__ = [
    "CELERY_CAPABILITIES",
    "DEFAULT_DISPATCH_TASK",
    "CeleryTaskBackend",
    "make_backend",
    "map_state",
]
