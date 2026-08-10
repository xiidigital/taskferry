"""``taskport_events`` — portable pub/sub and fan-out event delivery.

An *Event* declares that something happened; it may have **zero or more**
consumers (fan-out), unlike a Task, which is one operation to run (ADR-0003).
Plain Python, no Django required (section 57):

    from taskport_events import Event, publishers

    publishers["default"].publish(
        Event(type="file.uploaded", source="urn:svc:uploads", data={"file_id": "42"})
    )

The default publisher is an in-process fan-out bus (`InMemoryEventBus`). Provider
adapters (GCP Pub/Sub, and more on the roadmap) live under
``taskport_events.adapters`` and import their SDK lazily (section 31).
"""

from __future__ import annotations

from .adapters.inmemory import InMemoryEventBus, Subscription
from .capabilities import EventCapability
from .errors import EventError, PublishError
from .models import Event, PublishResult, event_attributes
from .publisher import BaseEventPublisher, EventPublisher
from .registry import publishers

__version__ = "0.1.0"

__all__ = [
    "BaseEventPublisher",
    "Event",
    "EventCapability",
    "EventError",
    "EventPublisher",
    "InMemoryEventBus",
    "PublishError",
    "PublishResult",
    "Subscription",
    "__version__",
    "event_attributes",
    "publishers",
]
