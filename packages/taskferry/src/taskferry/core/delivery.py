"""Delivery-semantics vocabulary (sections 21-22, ADR-0010).

Taskferry **never** promises exactly-once. Distributed systems produce
duplicates; the whole family assumes at-least-once with possible duplicates and
promotes idempotency. These enums let adapters *declare* their real semantics so
callers can reason about them instead of guessing.
"""

from __future__ import annotations

from enum import StrEnum


class DeliveryGuarantee(StrEnum):
    """How many times a message may be delivered."""

    AT_MOST_ONCE = "at_most_once"
    """Delivered zero or one time. Losses possible, duplicates impossible."""

    AT_LEAST_ONCE = "at_least_once"
    """Delivered one or more times. Losses impossible, duplicates possible."""

    # Note: EXACTLY_ONCE is intentionally absent. See ADR-0010.


class Ordering(StrEnum):
    """Ordering guarantee across a stream/queue/topic."""

    UNORDERED = "unordered"
    PER_KEY = "per_key"
    """Ordered within a partition/group key (e.g. FIFO message group)."""

    TOTAL = "total"


__all__ = ["DeliveryGuarantee", "Ordering"]
