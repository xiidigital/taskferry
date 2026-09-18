"""Provider metadata — the escape hatch for provider-specific facts.

Portable contracts stay clean; provider-specific truth (the real provider id,
region, resource name, arbitrary labels) lives here, attached to handles/results.
This is how Taskferry avoids "contaminating the common models with hundreds of
cloud options" (section 17) while still exposing them when needed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class ProviderMetadata:
    """Immutable description of where/how a resource actually lives.

    Attributes:
        provider: Short provider key, e.g. ``"local"``, ``"gcp"``, ``"aws"``,
            ``"azure"``, ``"kubernetes"``.
        provider_id: The provider's own identifier (task name, execution name,
            message id, schedule name). May be ``None`` before submission.
        region: Provider region/location when meaningful.
        resource: Fully-qualified provider resource name when meaningful.
        labels: Arbitrary provider-specific key/value metadata.
    """

    provider: str
    provider_id: str | None = None
    region: str | None = None
    resource: str | None = None
    labels: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Freeze the mapping so a frozen dataclass is actually immutable.
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))

    def with_provider_id(self, provider_id: str) -> ProviderMetadata:
        """Return a copy carrying the resolved provider id."""
        return replace(self, provider_id=provider_id)

    def with_labels(self, **labels: str) -> ProviderMetadata:
        """Return a copy with additional labels merged in."""
        merged = {**self.labels, **labels}
        return replace(self, labels=merged)


__all__ = ["ProviderMetadata"]
