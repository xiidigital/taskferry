"""Event-domain errors, rooted in the shared Taskport hierarchy (section 33)."""

from __future__ import annotations

from taskport.core import TaskportError


class EventError(TaskportError):
    """Base class for event-domain errors."""


class PublishError(EventError):
    """Raised when an event could not be published."""


__all__ = ["EventError", "PublishError"]
