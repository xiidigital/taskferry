"""The ``publishers`` registry (sections 35, 11).

``from taskport_events import publishers`` then ``publishers["default"]``. An
in-memory bus is registered as ``"default"`` out of the box; applications
reconfigure it (and add provider publishers) via ``configure`` / ``register``.
Construction is lazy, so no provider SDK is imported until first access.
"""

from __future__ import annotations

from taskport.core import LazyRegistry

from .adapters.inmemory import make_in_memory_event_bus
from .publisher import EventPublisher

publishers: LazyRegistry[EventPublisher] = LazyRegistry("event publisher")

# Zero-config default: an in-process bus (useful for tests and single-process use).
publishers.register("default", make_in_memory_event_bus)

__all__ = ["publishers"]
