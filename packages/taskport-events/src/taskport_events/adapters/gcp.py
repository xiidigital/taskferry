"""PubSubPublisher — publish events to a Google Cloud Pub/Sub topic.

The Google SDK is imported lazily; the publisher client can be injected for
testing (ADR-0008). Events are serialized as CloudEvents-structured JSON with
portable string attributes for subscription-side filtering.
"""

from __future__ import annotations

from typing import Any

from taskport.core import (
    CapabilitySet,
    ConfigurationError,
    JsonSerializer,
    ProviderError,
    ProviderMetadata,
)

from ..capabilities import EventCapability
from ..models import Event, PublishResult, event_attributes
from ..publisher import BaseEventPublisher

_SERIALIZER = JsonSerializer()

_PUBSUB_CAPABILITIES = frozenset(
    {
        EventCapability.FANOUT,
        EventCapability.DELIVERY_RETRY,
        EventCapability.DEAD_LETTER,
        EventCapability.FILTERING,
        EventCapability.RETENTION,
    }
)


class PubSubPublisher(BaseEventPublisher):
    """Publishes events to a Pub/Sub topic."""

    def __init__(
        self,
        *,
        project: str,
        topic: str,
        client: Any = None,
        ordering: bool = False,
    ) -> None:
        self._project = project
        self._topic = topic
        self._client = client
        self._ordering = ordering

    @property
    def provider(self) -> str:
        return "gcp"

    @property
    def capabilities(self) -> CapabilitySet:
        caps = set(_PUBSUB_CAPABILITIES)
        if self._ordering:
            caps.add(EventCapability.ORDERING)
        return CapabilitySet(caps, provider="gcp")

    def _publisher(self) -> Any:
        if self._client is None:
            try:
                from google.cloud import pubsub_v1
            except ImportError as exc:  # pragma: no cover
                raise ProviderError(
                    "google-cloud-pubsub is required for PubSubPublisher; "
                    "install taskport-events[gcp]",
                    provider="gcp",
                ) from exc
            self._client = pubsub_v1.PublisherClient()
        return self._client

    def _publish(self, event: Event) -> PublishResult:
        if not self._topic:
            raise ConfigurationError("PubSubPublisher requires a topic")
        client = self._publisher()
        topic_path = client.topic_path(self._project, self._topic)
        payload = _SERIALIZER.dumps(event.to_cloudevent())
        attributes = dict(event_attributes(event))
        kwargs: dict[str, Any] = dict(attributes)
        if self._ordering and event.subject:
            kwargs["ordering_key"] = event.subject
        try:
            future = client.publish(topic_path, data=payload, **kwargs)
            message_id = future.result()
        except Exception as exc:
            raise ProviderError(f"publish failed: {exc}", provider="gcp") from exc
        metadata = ProviderMetadata(
            provider="gcp",
            provider_id=str(message_id),
            resource=topic_path,
        )
        return PublishResult(event_id=event.id, provider_metadata=metadata)


def make_pubsub_publisher(**kwargs: object) -> PubSubPublisher:
    return PubSubPublisher(
        project=str(kwargs["project"]),
        topic=str(kwargs["topic"]),
        ordering=bool(kwargs.get("ordering", False)),
    )


__all__ = ["PubSubPublisher", "make_pubsub_publisher"]
