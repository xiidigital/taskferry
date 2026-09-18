"""The Procrastinate `TaskBackend`.

Translation, in both directions, and nothing else.

```mermaid
flowchart LR
    subgraph Taskferry
        TS["TaskSpec<br/>queue · priority · delay · retry · idempotency_key"]
        EX["Execution<br/>ExecutionState"]
    end

    subgraph Procrastinate
        DEF["configure_task(...).defer()"]
        JOB["job status<br/>todo · doing · succeeded · failed · cancelled"]
    end

    TS --> DEF
    JOB --> EX
```

Capabilities are advertised from what Procrastinate genuinely does:

* ``DELAY`` — ``schedule_at`` / ``schedule_in``;
* ``PRIORITY`` — native job priority;
* ``RETRY`` — the engine owns retries. The portable policy travels in the
  message and the dispatcher turns a failure into Procrastinate's own retry
  signal, so the job is genuinely re-queued by Procrastinate with the requested
  delay. Taskferry never loops in-process pretending to retry;
* ``DEDUPLICATION`` — via ``queueing_lock``, which really does refuse a second
  job while the first is pending;
* ``STATE`` — through the job manager.

``RESULT`` is **not** advertised. Procrastinate is fire-and-forget by design: it
does not store a job's return value. Claiming otherwise here would mean inventing
a results table, which is a queue feature, and building queue features is the
thing this project exists not to do. Applications that need results write them
where they belong — the database row the task updated, an object store, a cache.
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
from taskferry.tracking import ExternalIdIndex, is_digits

from .message import build_message

DEFAULT_DISPATCH_TASK = "taskferry:dispatch"
"""Name of the single Procrastinate task that runs every Taskferry message."""

PROCRASTINATE_CAPABILITIES = frozenset(
    {
        Capability.SUBMIT,
        Capability.STATE,
        Capability.CANCEL,
        Capability.DELAY,
        Capability.PRIORITY,
        Capability.RETRY,
        Capability.DEDUPLICATION,
    }
)

#: Procrastinate job status -> portable state. ``aborting``/``aborted`` appear in
#: newer releases; both map to the Taskferry state that describes what an operator
#: actually sees, not the internal step.
_STATUS_MAP: dict[str, ExecutionState] = {
    "todo": ExecutionState.QUEUED,
    "doing": ExecutionState.RUNNING,
    "succeeded": ExecutionState.SUCCEEDED,
    "failed": ExecutionState.FAILED,
    "cancelled": ExecutionState.CANCELLED,
    "aborting": ExecutionState.RUNNING,
    "aborted": ExecutionState.CANCELLED,
}


def map_status(status: object) -> ExecutionState:
    """Map a Procrastinate job status to a portable state.

    Pure, so it is unit-testable without a database. An unrecognised status maps
    to ``UNKNOWN`` rather than to a guess — a new Procrastinate release adding a
    status must not make Taskferry report something false.
    """
    if status is None:
        return ExecutionState.UNKNOWN
    key = getattr(status, "value", status)
    return _STATUS_MAP.get(str(key).lower(), ExecutionState.UNKNOWN)


class ProcrastinateTaskBackend(BaseBackend):
    """Defers Taskferry tasks as Procrastinate jobs.

    Args:
        app: A ``procrastinate.App``, or a ``"module:attribute"`` string resolved
            on first use. A string keeps this backend constructible in a process
            that has not yet configured its database connection.
        dispatch_task: Name of the registered dispatcher task. Only change it if
            you registered the dispatcher under a different name.
        default_lock: ``lock`` applied when a spec does not set one. Procrastinate
            serialises jobs sharing a lock, which is how you stop two workers
            touching the same row.

    Backend options, under the ``"procrastinate"`` namespace::

        TaskSpec(
            task="myapp.tasks:reindex",
            backend_options=BackendOptions({"procrastinate": {
                "lock": "reindex-42",        # serialise against this key
                "queueing_lock": "reindex",  # refuse a duplicate while pending
            }}),
        )

    Thread-safe once constructed: it holds a Procrastinate ``App``, which is
    designed to be shared, and no mutable state of its own.
    """

    def __init__(
        self,
        *,
        app: Any = None,
        dispatch_task: str = DEFAULT_DISPATCH_TASK,
        default_lock: str | None = None,
        name: str = "procrastinate",
        tracked_ids: int = 10_000,
    ) -> None:
        self._app = app
        self._dispatch_task = dispatch_task
        self._default_lock = default_lock
        self._name = name
        # Procrastinate knows nothing about Taskferry ids, so remember the
        # pairing for the executions this process submitted; a bare numeric id
        # from a dashboard is accepted too.
        self._ids = ExternalIdIndex(capacity=tracked_ids, recognises=is_digits)

    @property
    def name(self) -> str:
        return self._name

    @property
    def kind(self) -> ExecutionKind:
        return ExecutionKind.TASK

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(PROCRASTINATE_CAPABILITIES, provider=self._name)

    # -- the app ---------------------------------------------------------------- #
    def app(self) -> Any:
        """The Procrastinate ``App``, resolving a dotted path on first use."""
        if self._app is None:
            raise ConfigurationError(
                f"{self._name!r} needs a procrastinate App: pass app=<App> or "
                "app='myapp.tasks:app' in the backend options"
            )
        if isinstance(self._app, str):
            self._app = _import_attribute(self._app)
        return self._app

    # -- submission --------------------------------------------------------------- #
    def _submit(self, spec: ExecutionSpec) -> Execution:
        assert isinstance(spec, TaskSpec)
        # Resolve the app first, outside the try below. A missing or unresolvable
        # App is a ConfigurationError and must stay one — reporting it as "the
        # engine refused the submission" sends whoever is debugging to look at
        # PostgreSQL when the problem is a settings file.
        app = self.app()
        options = spec.options_for("procrastinate")
        deferrer_kwargs: dict[str, Any] = {
            "name": self._dispatch_task,
            "queue": spec.queue,
            "priority": spec.priority,
            "schedule_at": spec.scheduled_for(),
        }

        lock = options.get("lock", self._default_lock)
        if lock is not None:
            deferrer_kwargs["lock"] = str(lock)

        # An idempotency key maps onto queueing_lock, which is Procrastinate's
        # real mechanism for "do not enqueue this twice while one is pending".
        # It is honest deduplication, not an exactly-once promise.
        queueing_lock = options.get("queueing_lock", spec.idempotency_key)
        if queueing_lock is not None:
            deferrer_kwargs["queueing_lock"] = str(queueing_lock)

        for key, value in options.items():
            if key not in {"lock", "queueing_lock"}:
                deferrer_kwargs[key] = value

        message = build_message(spec)
        try:
            deferrer = app.configure_task(**deferrer_kwargs)
            job_id = deferrer.defer(message=message)
        except Exception as exc:
            raise SubmissionError(
                f"procrastinate could not defer {spec.task!r} on queue {spec.queue!r}: {exc}",
                backend=self._name,
            ) from exc

        now = datetime.now(UTC)
        execution_id = new_execution_id(ExecutionKind.TASK)
        self._ids.remember(str(execution_id), str(job_id))
        return Execution(
            id=execution_id,
            kind=ExecutionKind.TASK,
            backend=self._name,
            state=ExecutionState.QUEUED,
            name=spec.name,
            created_at=now,
            external_id=str(job_id),
            correlation=spec.correlation,
            provider_metadata=ProviderMetadata(
                provider="postgres",
                provider_id=str(job_id),
                resource=f"queue:{spec.queue}",
                labels=dict(spec.labels),
            ),
            metadata={"queue": spec.queue, "procrastinate_job_id": str(job_id)},
        )

    # -- observation ---------------------------------------------------------------- #
    def _get(self, execution_id: ExecutionId) -> Execution:
        """Look the job up by the Procrastinate id recorded at submission.

        Taskferry ids are Taskferry's; Procrastinate never sees one. So ``get``
        accepts either — a Taskferry id when this process submitted it, or the
        engine's own numeric id, which is what an operator reads off a dashboard.
        """
        job = self._find_job(str(execution_id))
        if job is None:
            raise ExecutionNotFound(
                f"procrastinate has no job {execution_id!r}; pass the procrastinate job id "
                "when the execution was submitted by another process",
                backend=self._name,
            )
        return self._execution_from_job(job, execution_id)

    def _find_job(self, execution_id: str) -> Any:
        numeric = self._ids.resolve(execution_id)
        if numeric is None:
            return None
        manager = getattr(self.app(), "job_manager", None)
        if manager is None:  # pragma: no cover - very old procrastinate
            raise ExecutionNotFound(
                "this procrastinate App has no job_manager, so state cannot be read",
                backend=self._name,
            )
        jobs = list(manager.list_jobs(id=int(numeric)))
        return jobs[0] if jobs else None

    def _execution_from_job(self, job: Any, execution_id: ExecutionId) -> Execution:
        return Execution(
            id=execution_id,
            kind=ExecutionKind.TASK,
            backend=self._name,
            state=map_status(getattr(job, "status", None)),
            name=str(getattr(job, "task_name", "") or ""),
            external_id=str(getattr(job, "id", "")),
            attempt=int(getattr(job, "attempts", 0) or 0) + 1,
            provider_metadata=ProviderMetadata(
                provider="postgres",
                provider_id=str(getattr(job, "id", "")),
                resource=f"queue:{getattr(job, 'queue_name', '')}",
            ),
            metadata={"queue": str(getattr(job, "queue_name", "") or "")},
        )

    def _cancel(self, execution_id: ExecutionId) -> Execution:
        """Cancel through Procrastinate's own job manager.

        A job that has already been picked up cannot be un-run; Procrastinate
        reports that by refusing, and the refreshed state says what really
        happened rather than claiming success.
        """
        numeric = self._ids.resolve(str(execution_id))
        if numeric is None:
            raise ExecutionNotFound(
                f"{execution_id!r} was not submitted by this backend instance; pass the "
                "procrastinate job id to cancel it from another process",
                backend=self._name,
            )
        manager = self.app().job_manager
        try:
            manager.cancel_job_by_id(int(numeric))
        except Exception as exc:
            raise SubmissionError(
                f"procrastinate could not cancel job {numeric}: {exc}", backend=self._name
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
        raise ConfigurationError(f"cannot resolve procrastinate app {path!r}: {exc}") from exc


def make_backend(**options: Any) -> ProcrastinateTaskBackend:
    """Entry point for ``{"factory": "procrastinate", ...}`` configuration."""
    return ProcrastinateTaskBackend(
        app=options.get("app"),
        dispatch_task=str(options.get("dispatch_task", DEFAULT_DISPATCH_TASK)),
        default_lock=options.get("default_lock"),
        name=str(options.get("name", "procrastinate")),
        tracked_ids=int(options.get("tracked_ids", 10_000)),
    )


__all__ = [
    "DEFAULT_DISPATCH_TASK",
    "PROCRASTINATE_CAPABILITIES",
    "ProcrastinateTaskBackend",
    "make_backend",
    "map_status",
]
