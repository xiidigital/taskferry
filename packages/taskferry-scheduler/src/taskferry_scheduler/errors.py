"""Scheduler-domain errors, rooted in the shared Taskferry hierarchy (section 33)."""

from __future__ import annotations

from taskferry.core import TaskferryError


class ScheduleError(TaskferryError):
    """Base class for scheduler-domain errors."""


class ScheduleNotFoundError(ScheduleError):
    """Raised when a schedule handle refers to an unknown schedule."""


__all__ = ["ScheduleError", "ScheduleNotFoundError"]
