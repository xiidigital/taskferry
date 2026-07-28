"""Azure Event Grid publisher.

Targets ``azure-eventgrid`` with ``azure-identity`` (or a topic key). The client
is imported lazily and can be injected for testing. Events are sent in
CloudEvents schema — the format Taskport already speaks.
"""

from __future__ import annotations

from typing import Any

from taskport.core import (
    CapabilitySet,
    ConfigurationError,
    ProviderError,
    ProviderMetadata,
)

from ..capabilities import EventCapability
from ..models import Event, PublishResult
from ..publisher import BaseEventPublisher

_EVENTGRID_CAPABILITIES = frozenset(
    {
        EventCapability.FANOUT,
        EventCapability.DELIVERY_RETRY,
        EventCapability.DEAD_LETTER,
        EventCapability.FILTERING,
        EventCapability.RETENTION,
    }
)


class EventGridPublisher(BaseEventPublisher):
    """Publishes events to an Azure Event Grid topic (CloudEvents schema)."""

    def __init__(
        self,
        *,
        endpoint: str,
        credential: Any = None,
        client: Any = None,
    ) -> None:
        self._endpoint = endpoint
        self._credential = credential
        self._client = client

    @property
    def provider(self) -> str:
        return "azure"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(_EVENTGRID_CAPABILITIES, provider="azure")

    def _publisher(self) -> Any:
        if self._client is None:
            if not self._endpoint:
                raise ConfigurationError("EventGridPublisher requires an endpoint")
            try:
                from azure.eventgrid import (
                    EventGridPublisherClient,
                )
                from azure.identity import DefaultAzureCredential
            except ImportError as exc:  # pragma: no cover
                raise ProviderError(
                    "azure-eventgrid and azure-identity are required for "
                    "EventGridPublisher; install taskport-events[azure]",
                    provider="azure",
                ) from exc
            credential = self._credential or DefaultAzureCredential()
            self._client = EventGridPublisherClient(self._endpoint, credential)
        return self._client

    def _publish(self, event: Event) -> PublishResult:
        cloud_event = event.to_cloudevent()
        try:
            self._publisher().send([cloud_event])
        except Exception as exc:
            raise ProviderError(f"eventgrid send failed: {exc}", provider="azure") from exc
        # Event Grid does not return a per-event id; the Taskport id stands in.
        metadata = ProviderMetadata(
            provider="azure",
            provider_id=event.id,
            resource=self._endpoint,
        )
        return PublishResult(event_id=event.id, provider_metadata=metadata)


def make_eventgrid_publisher(**kwargs: object) -> EventGridPublisher:
    return EventGridPublisher(endpoint=str(kwargs["endpoint"]))


__all__ = ["EventGridPublisher", "make_eventgrid_publisher"]
