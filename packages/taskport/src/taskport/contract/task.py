"""The contract every :class:`~taskport.ports.TaskBackend` must satisfy."""

from __future__ import annotations

from abc import abstractmethod
from datetime import timedelta

import pytest

from ..capabilities import Capability
from ..errors import UnsupportedCapability
from ..execution import ExecutionKind
from ..ports import ExecutionBackend
from ..specs import TaskSpec
from .base import ExecutionBackendContract


class TaskBackendContract(ExecutionBackendContract):
    """Subclass in an adapter's test module and implement the two hooks.

    ::

        class TestProcrastinate(TaskBackendContract):
            def make_backend(self):
                return ProcrastinateTaskBackend(app=FakeApp())

            def success_spec(self):
                return TaskSpec(task="tests.tasks:ok", args=(1, 2))
    """

    @abstractmethod
    def make_backend(self) -> ExecutionBackend: ...

    @abstractmethod
    def success_spec(self) -> TaskSpec: ...

    def test_is_a_task_backend(self) -> None:
        assert self.make_backend().kind is ExecutionKind.TASK

    def test_delay_capability_matches_deferred_specs(self) -> None:
        """A backend must either honour ``delay`` or refuse it outright."""
        backend = self.make_backend()
        deferred = self.success_spec().evolve(delay=timedelta(seconds=30))
        if Capability.DELAY in backend.capabilities:
            execution = backend.submit(deferred)
            assert execution.id
        else:
            with pytest.raises(UnsupportedCapability):
                backend.submit(deferred)

    def test_priority_capability_matches_prioritised_specs(self) -> None:
        backend = self.make_backend()
        prioritised = self.success_spec().evolve(priority=5)
        if Capability.PRIORITY in backend.capabilities:
            assert backend.submit(prioritised).id
        else:
            with pytest.raises(UnsupportedCapability):
                backend.submit(prioritised)

    def test_deduplication_capability_matches_idempotency_keys(self) -> None:
        """``idempotency_key`` is a request, and a backend that cannot honour it says so."""
        backend = self.make_backend()
        keyed = self.success_spec().evolve(idempotency_key="contract-key-1")
        if Capability.DEDUPLICATION in backend.capabilities:
            assert backend.submit(keyed).id
        else:
            with pytest.raises(UnsupportedCapability):
                backend.submit(keyed)

    def test_backend_options_for_other_backends_are_ignored(self) -> None:
        """One spec must travel unchanged across engines.

        A spec carrying options for a *different* backend has to submit cleanly —
        otherwise moving a queue from Procrastinate to Cloud Tasks would mean
        editing every call site, which is the coupling Taskport exists to remove.
        """
        from ..specs import BackendOptions

        backend = self.make_backend()
        spec = self.success_spec().evolve(
            backend_options=BackendOptions({"some-other-engine": {"nonsense": True}})
        )
        assert backend.submit(spec).id

    def test_arguments_survive_the_spec_unchanged(self) -> None:
        spec = self.success_spec()
        assert spec.args == tuple(spec.args)
        assert dict(spec.kwargs) == dict(spec.kwargs)


__all__ = ["TaskBackendContract"]
