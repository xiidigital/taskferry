"""Event-domain errors, rooted in the shared Taskferry hierarchy (section 33)."""

from __future__ import annotations

from taskferry.core import TaskferryError


class EventError(TaskferryError):
    """Base class for event-domain errors."""


class PublishError(EventError):
    """Raised when an event could not be published."""


__all__ = ["EventError", "PublishError"]
