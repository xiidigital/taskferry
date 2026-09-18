"""The contract every :class:`~taskferry.ports.InlineBackend` must satisfy.

Inline is the strictest port: it runs in the caller's process, so there is
nowhere to hide. By the time ``submit`` returns, the work is finished.
"""

from __future__ import annotations

from abc import abstractmethod

import pytest

from ..capabilities import Capability
from ..execution import ExecutionKind, ExecutionState
from ..ports import ExecutionBackend
from ..specs import InlineSpec
from .base import ExecutionBackendContract


def _echo(value: object = "ok") -> object:
    return value


def _explode() -> None:
    raise ValueError("contract failure probe")


class InlineBackendContract(ExecutionBackendContract):
    """Subclass in a test module and implement :meth:`make_backend`."""

    reaches_terminal_state = True

    @abstractmethod
    def make_backend(self) -> ExecutionBackend: ...

    def success_spec(self) -> InlineSpec:
        return InlineSpec(func=_echo, args=("ok",))

    def test_is_an_inline_backend(self) -> None:
        assert self.make_backend().kind is ExecutionKind.INLINE

    def test_submit_is_already_terminal(self) -> None:
        """Inline has no queue: submission and completion are the same moment."""
        backend = self.make_backend()
        execution = backend.submit(self.success_spec())
        assert execution.is_terminal
        assert execution.state is ExecutionState.SUCCEEDED

    def test_the_value_comes_back(self) -> None:
        backend = self.make_backend()
        execution = backend.submit(InlineSpec(func=_echo, args=(42,)))
        assert execution.result is not None
        assert execution.result.value == 42

    def test_a_failure_is_recorded_not_raised(self) -> None:
        """A failed execution is data, not an exception at the submit call site.

        The exception surfaces from ``handle.result()``, so callers choose when
        to care — the same as for a task that fails on a worker.
        """
        backend = self.make_backend()
        execution = backend.submit(InlineSpec(func=_explode))
        assert execution.state is ExecutionState.FAILED
        assert execution.result is not None
        assert execution.result.error_type == "ValueError"
        assert execution.result.traceback

    def test_closures_and_lambdas_are_accepted(self) -> None:
        """The point of inline: no importable name is required."""
        backend = self.make_backend()
        offset = 5
        execution = backend.submit(InlineSpec(func=lambda x: x + offset, args=(1,)))
        assert execution.result is not None
        assert execution.result.value == 6

    async def test_async_callables_are_awaited(self) -> None:
        backend = self.make_backend()
        if Capability.ASYNC_CALLABLE not in backend.capabilities:
            pytest.skip("backend does not claim to run async callables")

        async def coro(value: int) -> int:
            return value * 2

        execution = backend.submit(InlineSpec(func=coro, args=(21,)))
        assert execution.result is not None
        assert execution.result.value == 42


__all__ = ["InlineBackendContract"]
