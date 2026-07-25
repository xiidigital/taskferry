"""Portable event domain types (CloudEvents-inspired).

An *Event* is a statement that something happened, with **zero or more**
consumers — distinct from a Task, which is one operation to run (ADR-0002,
sections 11, 41). The shape follows the CloudEvents attributes (``id``, ``type``,
``source``, ``subject``, ``time``, ``data``) so events interoperate with the
broader ecosystem. Data is JSON only (ADR-0008).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType

from taskport.core import (
    Correlation,
    JSONObject,
    ProviderMetadata,
    TaskportId,
    ensure_json_serializable,
    new_id,
)


@dataclass(frozen=True, slots=True)
class Event:
    """An immutable record that something happened.

    Attributes:
        id: Taskport-owned event id.
        type: Reverse-DNS-ish event type, e.g. ``"resource.created"``.
        source: Logical origin, e.g. ``"urn:service:catalog"``.
        data: JSON payload (identifiers, not objects).
        subject: Optional subject the event concerns (e.g. a resource id).
        time: Occurrence time (UTC).
        specversion / datacontenttype: CloudEvents metadata.
        correlation: Correlation for flow tracking (section 19).
        provider_metadata: Set by the publisher after delivery.
    """

    type: str
    source: str
    id: TaskportId = field(default_factory=lambda: new_id("evt"))
    data: JSONObject = field(default_factory=dict)
    subject: str | None = None
    time: datetime = field(default_factory=lambda: datetime.now(UTC))
    specversion: str = "1.0"
    datacontenttype: str = "application/json"
    correlation: Correlation | None = None
    provider_metadata: ProviderMetadata | None = None

    def __post_init__(self) -> None:
        ensure_json_serializable(dict(self.data))
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))

    def to_cloudevent(self) -> dict[str, object]:
        """Serialize to a JSON-safe CloudEvents-style structured dict."""
        envelope: dict[str, object] = {
            "specversion": self.specversion,
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "time": self.time.isoformat(),
            "datacontenttype": self.datacontenttype,
            "data": dict(self.data),
        }
        if self.subject is not None:
            envelope["subject"] = self.subject
        if self.correlation is not None:
            envelope["taskportcorrelation"] = self.correlation.to_headers()
        return envelope


@dataclass(frozen=True, slots=True)
class PublishResult:
    """Outcome of publishing a single event."""

    event_id: TaskportId
    provider_metadata: ProviderMetadata

    @property
    def provider(self) -> str:
        return self.provider_metadata.provider


def event_attributes(event: Event) -> Mapping[str, str]:
    """Portable string attributes for provider message metadata/filtering."""
    attributes = {
        "taskport-event-type": event.type,
        "taskport-event-source": event.source,
    }
    if event.subject is not None:
        attributes["taskport-event-subject"] = event.subject
    if event.correlation is not None:
        attributes.update(event.correlation.to_headers())
    return attributes


__all__ = ["Event", "PublishResult", "event_attributes"]
