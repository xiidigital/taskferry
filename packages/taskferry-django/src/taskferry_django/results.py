"""Django TaskResult extension for Taskferry identity metadata."""

from __future__ import annotations

from dataclasses import dataclass, field

from django.tasks.base import TaskResult


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskferryTaskResult(TaskResult):
    """Django result with portable and provider ids kept distinct.

    ``id`` remains the Taskferry execution id. ``provider_id`` is the native
    queue/task identifier reported at enqueue time; it does not imply that the
    backend can later retrieve state or results.
    """

    provider_id: str | None = None
    _current_attempt: int | None = field(default=None, repr=False, compare=False)

    @property
    def attempts(self) -> int:
        """Use the active delivery count in TaskContext; otherwise Django's count."""
        if self._current_attempt is not None:
            return self._current_attempt
        return len(self.worker_ids)


__all__ = ["TaskferryTaskResult"]
