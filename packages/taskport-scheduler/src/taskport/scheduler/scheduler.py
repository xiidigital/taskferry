"""The Scheduler port and a base class enforcing capability honesty."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from taskport.core import (
    ATTR_PROVIDER,
    ATTR_SCHEDULE_ID,
    SPAN_SCHEDULE_CREATE,
    CapabilitySet,
    span,
)

from .capabilities import ScheduleCapability
from .models import Schedule, ScheduleHandle
from .triggers import CronTrigger


@runtime_checkable
class Scheduler(Protocol):
    """Port for managing schedules. Adapters implement this contract."""

    @property
    def provider(self) -> str: ...

    @property
    def capabilities(self) -> CapabilitySet: ...

    def create(self, schedule: Schedule) -> ScheduleHandle: ...

    def get(self, handle: ScheduleHandle) -> ScheduleHandle: ...

    def list(self) -> list[ScheduleHandle]: ...

    def pause(self, handle: ScheduleHandle) -> ScheduleHandle: ...

    def resume(self, handle: ScheduleHandle) -> ScheduleHandle: ...

    def update(self, handle: ScheduleHandle, schedule: Schedule) -> ScheduleHandle: ...

    def delete(self, handle: ScheduleHandle) -> None: ...


class BaseScheduler(ABC):
    """Template base applying capability checks and tracing."""

    @property
    @abstractmethod
    def provider(self) -> str: ...

    @property
    @abstractmethod
    def capabilities(self) -> CapabilitySet: ...

    @abstractmethod
    def _create(self, schedule: Schedule) -> ScheduleHandle: ...

    @abstractmethod
    def _get(self, handle: ScheduleHandle) -> ScheduleHandle: ...

    @abstractmethod
    def _list(self) -> list[ScheduleHandle]: ...

    @abstractmethod
    def _pause(self, handle: ScheduleHandle) -> ScheduleHandle: ...

    @abstractmethod
    def _resume(self, handle: ScheduleHandle) -> ScheduleHandle: ...

    @abstractmethod
    def _update(self, handle: ScheduleHandle, schedule: Schedule) -> ScheduleHandle: ...

    @abstractmethod
    def _delete(self, handle: ScheduleHandle) -> None: ...

    # -- public API with capability enforcement ---------------------------- #
    def _validate(self, schedule: Schedule) -> None:
        self.capabilities.require(schedule.trigger.required_capability())
        trigger = schedule.trigger
        if isinstance(trigger, CronTrigger) and trigger.timezone not in ("", "UTC"):
            self.capabilities.require(ScheduleCapability.TIMEZONE)

    def create(self, schedule: Schedule) -> ScheduleHandle:
        self._validate(schedule)
        with span(SPAN_SCHEDULE_CREATE, {ATTR_PROVIDER: self.provider}) as s:
            handle = self._create(schedule)
            s.set_attribute(ATTR_SCHEDULE_ID, handle.id)
            return handle

    def get(self, handle: ScheduleHandle) -> ScheduleHandle:
        return self._get(handle)

    def list(self) -> list[ScheduleHandle]:
        return self._list()

    def pause(self, handle: ScheduleHandle) -> ScheduleHandle:
        self.capabilities.require(ScheduleCapability.PAUSE)
        return self._pause(handle)

    def resume(self, handle: ScheduleHandle) -> ScheduleHandle:
        self.capabilities.require(ScheduleCapability.RESUME)
        return self._resume(handle)

    def update(self, handle: ScheduleHandle, schedule: Schedule) -> ScheduleHandle:
        self.capabilities.require(ScheduleCapability.UPDATE)
        self._validate(schedule)
        return self._update(handle, schedule)

    def delete(self, handle: ScheduleHandle) -> None:
        self.capabilities.require(ScheduleCapability.DELETE)
        self._delete(handle)


__all__ = ["BaseScheduler", "Scheduler"]
