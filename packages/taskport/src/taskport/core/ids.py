"""Portable Taskport identifiers.

A Taskport id is always Taskport-owned and never *is* the provider id. Adapters
keep the provider id alongside it in :class:`~taskport.core.provider.ProviderMetadata`
so a flow can be followed across systems (sections 20, 48).
"""

from __future__ import annotations

import uuid
from typing import NewType

# A distinct type so a Taskport id is never accidentally confused with a provider
# id or an arbitrary string in signatures.
TaskportId = NewType("TaskportId", str)

_ID_PREFIXES = frozenset({"tp", "task", "job", "evt", "sch", "run", "corr"})


def new_id(prefix: str = "tp") -> TaskportId:
    """Return a fresh, URL-safe, sortable-enough Taskport id.

    The prefix is advisory and only aids human debugging (e.g. ``job_9f2c...``).
    Uses uuid4 for collision resistance without coordination.
    """
    if not prefix or not prefix.isidentifier():
        raise ValueError(f"id prefix must be a valid identifier, got {prefix!r}")
    return TaskportId(f"{prefix}_{uuid.uuid4().hex}")


def is_taskport_id(value: str) -> bool:
    """Best-effort check that ``value`` looks like a Taskport id."""
    prefix, _, rest = value.partition("_")
    return bool(rest) and prefix.isidentifier()


__all__ = ["TaskportId", "is_taskport_id", "new_id"]
