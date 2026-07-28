"""Reusable contract test suite for :class:`Scheduler` implementations (section 37).

Subclass and provide a scheduler plus a schedule valid for it. Assertions are
capability-driven (ADR-0005). Requires ``pytest``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from taskport.core import CapabilitySet

from .capabilities import ScheduleCapability
from .models import Schedule, ScheduleHandle
from .scheduler import Scheduler


class SchedulerContract(ABC):
    """Subclass, implement ``make_scheduler`` and ``sample_schedule``."""

    @abstractmethod
    def make_scheduler(self) -> Scheduler:
        """Return a fresh scheduler under test (with a fake client if needed)."""

    @abstractmethod
    def sample_schedule(self) -> Schedule:
        """Return a schedule whose trigger the scheduler supports."""

    def test_capabilities_are_a_set_for_provider(self) -> None:
        scheduler = self.make_scheduler()
        caps = scheduler.capabilities
        assert isinstance(caps, CapabilitySet)
        assert caps.provider == scheduler.provider

    def test_create_returns_handle(self) -> None:
        scheduler = self.make_scheduler()
        handle = scheduler.create(self.sample_schedule())
        assert isinstance(handle, ScheduleHandle)
        assert handle.id.startswith("sch_")
        assert handle.provider == scheduler.provider

    def test_pause_resume_delete_match_capabilities(self) -> None:
        scheduler = self.make_scheduler()
        handle = scheduler.create(self.sample_schedule())
        caps = scheduler.capabilities
        if ScheduleCapability.PAUSE in caps:
            scheduler.pause(handle)
        if ScheduleCapability.RESUME in caps:
            scheduler.resume(handle)
        if ScheduleCapability.DELETE in caps:
            scheduler.delete(handle)


__all__ = ["SchedulerContract"]
