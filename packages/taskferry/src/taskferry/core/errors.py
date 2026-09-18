"""The Taskferry error hierarchy root.

Adapter distributions define their own leaf errors that subclass
:class:`TaskferryError`, so a caller can catch the whole family or one adapter.
The execution-layer hierarchy built on this root lives in :mod:`taskferry.errors`.
"""

from __future__ import annotations


class TaskferryError(Exception):
    """Base class for every error raised anywhere in the Taskferry family."""


class ConfigurationError(TaskferryError):
    """Raised when configuration is missing, malformed, or contradictory."""


class ProviderError(TaskferryError):
    """Wraps an error originating from an underlying provider/SDK.

    Adapters should raise this (chaining the original with ``from``) so callers
    can depend on a stable Taskferry type instead of provider-specific exceptions.
    """

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider


class UnsupportedCapabilityError(TaskferryError):
    """Raised when an operation requires a capability the provider lacks.

    Taskferry never silently simulates a missing capability (ADR-0005). Adapters
    fail loudly with this error, naming the capability and provider.
    """

    def __init__(self, capability: str, *, provider: str | None = None) -> None:
        provider_suffix = f" (provider={provider!r})" if provider else ""
        super().__init__(f"Unsupported capability: {capability!r}{provider_suffix}")
        self.capability = capability
        self.provider = provider


class SerializationError(TaskferryError):
    """Raised when a payload cannot be serialized to / from the wire format."""


__all__ = [
    "ConfigurationError",
    "ProviderError",
    "SerializationError",
    "TaskferryError",
    "UnsupportedCapabilityError",
]
