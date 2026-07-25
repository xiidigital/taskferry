"""The capability model shared by every Taskport domain (ADR-0005).

Providers do not support the same things. Taskport refuses to lie about this.
Each domain defines its own capability enum (a :class:`Capability` subclass) and
every provider exposes an immutable :class:`CapabilitySet`. Callers can:

* **feature-detect** — ``if cap in provider.capabilities: ...``
* **assert** — ``provider.capabilities.require(cap)`` raises
  :class:`~taskport.core.errors.UnsupportedCapabilityError` when absent.

A capability that does not exist is never ignored and never faked.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from enum import StrEnum

from .errors import UnsupportedCapabilityError


class Capability(StrEnum):
    """Base class for all domain capability enums.

    Subclassed per domain (``TaskCapability``, ``JobCapability``, ...). Using a
    ``StrEnum`` keeps capabilities serializable and comparable to plain strings,
    which matters for logging, wire formats, and cross-package checks.
    """


class CapabilitySet:
    """An immutable, hashable set of capabilities advertised by a provider."""

    __slots__ = ("_caps", "_provider")

    def __init__(self, capabilities: Iterable[Capability], *, provider: str | None = None) -> None:
        self._caps: frozenset[Capability] = frozenset(capabilities)
        self._provider = provider

    @property
    def provider(self) -> str | None:
        return self._provider

    def __contains__(self, capability: object) -> bool:
        return capability in self._caps

    def __iter__(self) -> Iterator[Capability]:
        return iter(self._caps)

    def __len__(self) -> int:
        return len(self._caps)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, CapabilitySet):
            return self._caps == other._caps
        if isinstance(other, (set, frozenset)):
            return self._caps == frozenset(other)
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._caps)

    def __repr__(self) -> str:
        names = ", ".join(sorted(c.value for c in self._caps))
        provider = f" provider={self._provider!r}" if self._provider else ""
        return f"CapabilitySet({{{names}}}{provider})"

    def supports(self, capability: Capability) -> bool:
        """Return whether ``capability`` is advertised."""
        return capability in self._caps

    def supports_all(self, capabilities: Iterable[Capability]) -> bool:
        return frozenset(capabilities) <= self._caps

    def require(self, capability: Capability) -> None:
        """Raise :class:`UnsupportedCapabilityError` unless ``capability`` is present."""
        if capability not in self._caps:
            raise UnsupportedCapabilityError(str(capability), provider=self._provider)

    def require_all(self, capabilities: Iterable[Capability]) -> None:
        for capability in capabilities:
            self.require(capability)

    def missing(self, capabilities: Iterable[Capability]) -> frozenset[Capability]:
        """Return the subset of ``capabilities`` that is *not* supported."""
        return frozenset(capabilities) - self._caps


__all__ = ["Capability", "CapabilitySet"]
