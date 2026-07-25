"""Reusable contract test suite for :class:`JobRunner` implementations.

Every runner adapter must pass this contract (section 37). Which assertions are
*mandatory* is decided by the runner's advertised capabilities (ADR-0005): a
runner that does not advertise ``CANCEL`` is asserted to *reject* cancellation,
not to perform it.

Usage (in a test module)::

    from taskport.jobs.contract import JobRunnerContract
    from taskport.jobs import LocalJobRunner, JobSpec

    class TestLocalRunner(JobRunnerContract):
        supports_real_execution = True

        def make_runner(self):
            return LocalJobRunner()

        def success_spec(self):
            return JobSpec(name="ok", command=["python", "-c", "pass"])

This module imports ``pytest`` and is intended for test environments only.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

import pytest

from taskport.core import CapabilitySet, UnsupportedCapabilityError

from .capabilities import JobCapability
from .models import JobResources, JobSpec, JobStatus
from .runner import JobRunner


class JobRunnerContract(ABC):
    """Subclass in a test module and implement the two hooks below."""

    #: Set True for runners that actually execute processes (e.g. local), so the
    #: full submit→poll→terminal lifecycle is exercised.
    supports_real_execution: bool = False

    @abstractmethod
    def make_runner(self) -> JobRunner:
        """Return a fresh runner under test."""

    @abstractmethod
    def success_spec(self) -> JobSpec:
        """Return a spec that should succeed on this runner."""

    # -- universal invariants ---------------------------------------------- #
    def test_capabilities_are_a_capability_set_for_provider(self) -> None:
        runner = self.make_runner()
        caps = runner.capabilities
        assert isinstance(caps, CapabilitySet)
        assert caps.provider == runner.provider

    def test_run_returns_handle_with_portable_and_provider_ids(self) -> None:
        runner = self.make_runner()
        handle = runner.run(self.success_spec())
        assert handle.id.startswith("job_")  # taskport-owned id
        assert handle.provider == runner.provider
        # get() must accept the handle and return a result bound to it.
        result = runner.get(handle)
        assert result.handle is handle
        assert isinstance(result.status, JobStatus)

    def test_unsupported_capability_is_rejected_not_faked(self) -> None:
        runner = self.make_runner()
        caps = runner.capabilities
        # Pick a capability the runner does NOT support and prove the matching
        # spec is rejected loudly.
        if JobCapability.GPU not in caps:
            spec = JobSpec(
                name="needs-gpu",
                command=["true"],
                image="img",
                resources=JobResources(gpu=1),
            )
            with pytest.raises(UnsupportedCapabilityError):
                runner.run(spec)
        else:  # pragma: no cover - depends on provider
            pytest.skip("runner supports GPU; nothing to assert here")

    def test_cancel_matches_capability(self) -> None:
        runner = self.make_runner()
        handle = runner.run(self.success_spec())
        if JobCapability.CANCEL in runner.capabilities:
            runner.cancel(handle)  # must not raise
        else:  # pragma: no cover - depends on provider
            with pytest.raises(UnsupportedCapabilityError):
                runner.cancel(handle)

    # -- lifecycle (only where real execution happens) --------------------- #
    def test_lifecycle_reaches_terminal(self) -> None:
        if not self.supports_real_execution:
            pytest.skip("runner does not execute real processes")
        runner = self.make_runner()
        handle = runner.run(self.success_spec())
        deadline = time.monotonic() + 10
        result = runner.get(handle)
        while not result.is_terminal and time.monotonic() < deadline:
            time.sleep(0.05)
            result = runner.get(handle)
        assert result.is_terminal
        assert result.status == JobStatus.SUCCEEDED


__all__ = ["JobRunnerContract"]
