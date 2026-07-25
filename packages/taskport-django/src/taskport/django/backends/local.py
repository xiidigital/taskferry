"""LocalBackend — development/testing backend.

Thin extension of Django's official ``ImmediateBackend`` (which runs the task
inline on enqueue) that adds the Taskport capability set. Use it for local
development; swap the ``BACKEND`` for a provider backend in production without
changing any ``@task`` code.
"""

from __future__ import annotations

from django.tasks.backends.immediate import ImmediateBackend

from ..base import TaskportCapabilityMixin


class LocalBackend(TaskportCapabilityMixin, ImmediateBackend):
    """Runs tasks immediately, in-process (dev/testing)."""

    provider = "local"


__all__ = ["LocalBackend"]
