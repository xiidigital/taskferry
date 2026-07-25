"""Job-domain errors, rooted in the shared Taskport hierarchy (section 33)."""

from __future__ import annotations

from taskport.core import TaskportError


class JobError(TaskportError):
    """Base class for job-domain errors."""


class JobNotFoundError(JobError):
    """Raised when a handle refers to a job the runner does not know about."""


__all__ = ["JobError", "JobNotFoundError"]
