"""Event publisher/bus capabilities (ADR-0005, section 13)."""

from __future__ import annotations

from taskferry.core import Capability


class EventCapability(Capability):
    """What an event backend can do. Providers advertise a subset."""

    FANOUT = "fanout"
    ORDERING = "ordering"
    DELIVERY_RETRY = "delivery_retry"
    DEAD_LETTER = "dead_letter"
    FILTERING = "filtering"
    REPLAY = "replay"
    RETENTION = "retention"


__all__ = ["EventCapability"]
