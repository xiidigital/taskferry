"""Scheduler-domain errors, rooted in the shared Taskport hierarchy (section 33)."""

from __future__ import annotations

from taskport.core import TaskportError


class ScheduleError(TaskportError):
    """Base class for scheduler-domain errors."""


class ScheduleNotFoundError(ScheduleError):
    """Raised when a schedule handle refers to an unknown schedule."""


__all__ = ["ScheduleError", "ScheduleNotFoundError"]
