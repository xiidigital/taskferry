"""Task backend capabilities (ADR-0005, section 13).

These map onto Django 6's backend feature flags (``supports_defer``,
``supports_async_task``, ``supports_get_result``, ``supports_priority``) plus
provider-specific abilities a backend may additionally advertise.
"""

from __future__ import annotations

from taskport.core import Capability


class TaskCapability(Capability):
    """What a Django Tasks backend can do. Backends advertise a subset."""

    PRIORITY = "priority"
    DELAY = "delay"
    SCHEDULED_EXECUTION = "scheduled_execution"
    RESULT_TRACKING = "result_tracking"
    CANCELLATION = "cancellation"
    QUEUE_SELECTION = "queue_selection"
    RETRIES = "retries"
    DEAD_LETTER = "dead_letter"
    ORDERING = "ordering"
    ASYNC_ENQUEUE = "async_enqueue"


__all__ = ["TaskCapability"]
