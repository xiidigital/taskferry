"""Invariants that hold for every backend, of every kind.

Subclassed by the per-kind suites; adapters normally use those rather than this
one directly.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable

import pytest

from ..capabilities import Capability, CapabilitySet
from ..errors import ExecutionNotFound, UnsupportedCapability
from ..execution import Execution, ExecutionId, ExecutionState
from ..ports import ExecutionBackend
from ..specs import AnySpec

WAIT_TIMEOUT = 30.0
"""How long the shared helpers wait for a real backend to reach a terminal state."""


class ExecutionBackendContract(ABC):
    """Assertions every :class:`~taskport.ports.ExecutionBackend` must satisfy."""

    #: Set True when the backend really executes work, so the suite can drive a
    #: full submit → poll → terminal lifecycle. Leave False for adapters tested
    #: against a fake provider client that never completes anything.
    reaches_terminal_state: bool = False

    @abstractmethod
    def make_backend(self) -> ExecutionBackend:
        """Return a fresh backend under test. Called once per test."""

    @abstractmethod
    def success_spec(self) -> AnySpec:
        """Return a spec this backend should accept and (if it executes) succeed on."""

    # -- identity ------------------------------------------------------------- #
    def test_declares_a_name_a_kind_and_capabilities(self) -> None:
        backend = self.make_backend()
        assert isinstance(backend.name, str) and backend.name, "a backend needs a stable name"
        caps = backend.capabilities
        assert isinstance(caps, CapabilitySet)
        assert caps.provider == backend.name, "capabilities must be attributed to the backend"
        assert Capability.SUBMIT in caps, "every backend can submit; say so explicitly"

    def test_kind_matches_the_specs_it_accepts(self) -> None:
        backend = self.make_backend()
        assert backend.kind is self.success_spec().kind

    # -- submission ------------------------------------------------------------ #
    def test_submit_returns_a_well_formed_execution(self) -> None:
        backend = self.make_backend()
        execution = backend.submit(self.success_spec())
        assert isinstance(execution, Execution)
        assert execution.id, "an execution must carry a Taskport-owned id"
        assert execution.id.startswith(f"{backend.kind.value}_"), (
            "execution ids are minted by taskport and prefixed with the kind"
        )
        assert execution.backend == backend.name
        assert execution.kind is backend.kind
        assert isinstance(execution.state, ExecutionState)

    def test_submit_rejects_a_spec_of_the_wrong_kind(self) -> None:
        backend = self.make_backend()
        wrong = _spec_of_another_kind(self.success_spec())
        with pytest.raises(TypeError):
            backend.submit(wrong)

    def test_provider_id_is_not_reused_as_the_taskport_id(self) -> None:
        """A Taskport id is Taskport's; the engine's id lives in ``external_id``."""
        backend = self.make_backend()
        execution = backend.submit(self.success_spec())
        if execution.external_id is not None:
            assert execution.external_id != execution.id

    # -- capabilities are honest ------------------------------------------------ #
    def test_state_capability_matches_get(self) -> None:
        backend = self.make_backend()
        execution = backend.submit(self.success_spec())
        if Capability.STATE in backend.capabilities:
            fetched = backend.get(execution.id)
            assert fetched.id == execution.id
            assert execution.transitions_to(fetched.state), (
                f"{execution.state} -> {fetched.state} is not a legal transition"
            )
        else:
            with pytest.raises(UnsupportedCapability):
                backend.get(execution.id)

    def test_cancel_capability_matches_cancel(self) -> None:
        backend = self.make_backend()
        execution = backend.submit(self.success_spec())
        if Capability.CANCEL in backend.capabilities:
            cancelled = backend.cancel(execution.id)
            assert cancelled.id == execution.id
        else:
            with pytest.raises(UnsupportedCapability):
                backend.cancel(execution.id)

    def test_result_capability_matches_result(self) -> None:
        backend = self.make_backend()
        execution = backend.submit(self.success_spec())
        if Capability.RESULT not in backend.capabilities:
            with pytest.raises(UnsupportedCapability):
                backend.result(execution.id)
        elif self.reaches_terminal_state:
            assert backend.result(execution.id, timeout=WAIT_TIMEOUT) is not None

    def test_unknown_id_raises_execution_not_found(self) -> None:
        backend = self.make_backend()
        if Capability.STATE not in backend.capabilities:
            pytest.skip("backend cannot report state, so there is nothing to look up")
        with pytest.raises(ExecutionNotFound):
            backend.get(ExecutionId("task_does_not_exist_0000"))

    # -- lifecycle (only where work really runs) -------------------------------- #
    def test_reaches_a_terminal_state(self) -> None:
        if not self.reaches_terminal_state:
            pytest.skip("backend does not execute work in this test environment")
        backend = self.make_backend()
        execution = backend.submit(self.success_spec())
        final = self.wait_for_terminal(backend, execution)
        assert final.is_terminal
        assert final.state is ExecutionState.SUCCEEDED, (
            f"success_spec() should succeed, got {final.state}: "
            f"{final.result.error if final.result else '<no result>'}"
        )
        assert final.finished_at is not None, "a terminal execution should report when it finished"

    def test_terminal_state_is_stable(self) -> None:
        """Re-reading a finished execution must not change it."""
        if not self.reaches_terminal_state:
            pytest.skip("backend does not execute work in this test environment")
        backend = self.make_backend()
        execution = backend.submit(self.success_spec())
        final = self.wait_for_terminal(backend, execution)
        again = backend.get(final.id)
        assert again.state is final.state

    # -- helper ------------------------------------------------------------------ #
    def wait_for_terminal(
        self, backend: ExecutionBackend, execution: Execution, timeout: float = WAIT_TIMEOUT
    ) -> Execution:
        """Poll until terminal, failing the test rather than hanging."""
        if execution.is_terminal:
            return execution
        deadline = time.monotonic() + timeout
        current = execution
        while time.monotonic() < deadline:
            current = backend.get(execution.id)
            if current.is_terminal:
                return current
            time.sleep(0.02)
        pytest.fail(f"{execution.id} never reached a terminal state (last: {current.state})")


def _spec_of_another_kind(spec: AnySpec) -> AnySpec:
    """Build a spec of a different kind, to prove the backend rejects it."""
    from ..execution import ExecutionKind
    from ..specs import InlineSpec, JobSpec, TaskSpec

    others: dict[ExecutionKind, Callable[[], AnySpec]] = {
        ExecutionKind.INLINE: lambda: InlineSpec(func=_noop),
        ExecutionKind.TASK: lambda: TaskSpec(task="taskport.contract.base:_noop"),
        ExecutionKind.JOB: lambda: JobSpec(job="wrong-kind", command=["true"]),
    }
    wrong_kind = next(kind for kind in others if kind is not spec.kind)
    return others[wrong_kind]()


def _noop() -> None:
    """Referenced by :func:`_spec_of_another_kind` so the reference resolves."""


__all__ = ["WAIT_TIMEOUT", "ExecutionBackendContract"]
