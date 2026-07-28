"""Kafka event publisher (confluent-kafka).

The producer is imported lazily and can be injected for testing. Events are
serialized as CloudEvents-structured JSON; ``event.subject`` becomes the message
key so a partition preserves per-key ordering.
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

_KAFKA_CAPABILITIES = frozenset(
    {
        EventCapability.FANOUT,
        EventCapability.ORDERING,
        EventCapability.DELIVERY_RETRY,
        EventCapability.RETENTION,
        EventCapability.REPLAY,
    }
)


class KafkaPublisher(BaseEventPublisher):
    """Publishes events to a Kafka topic."""

    def __init__(
        self,
        *,
        topic: str,
        producer: Any = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._topic = topic
        self._producer = producer
        self._config = config or {}

    @property
    def provider(self) -> str:
        return "kafka"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(_KAFKA_CAPABILITIES, provider="kafka")

    def _get_producer(self) -> Any:
        if self._producer is None:
            try:
                from confluent_kafka import Producer
            except ImportError as exc:  # pragma: no cover
                raise ProviderError(
                    "confluent-kafka is required for KafkaPublisher; "
                    "install taskport-events[kafka]",
                    provider="kafka",
                ) from exc
            self._producer = Producer(self._config)
        return self._producer

    def _publish(self, event: Event) -> PublishResult:
        if not self._topic:
            raise ConfigurationError("KafkaPublisher requires a topic")
        producer = self._get_producer()
        headers = list(event_attributes(event).items())
        try:
            producer.produce(
                self._topic,
                value=_SERIALIZER.dumps(event.to_cloudevent()),
                key=(event.subject or event.id).encode("utf-8"),
                headers=headers,
            )
            producer.flush()
        except Exception as exc:
            raise ProviderError(f"kafka produce failed: {exc}", provider="kafka") from exc
        metadata = ProviderMetadata(
            provider="kafka",
            provider_id=event.id,
            resource=self._topic,
        )
        return PublishResult(event_id=event.id, provider_metadata=metadata)


def make_kafka_publisher(**kwargs: object) -> KafkaPublisher:
    config = kwargs.get("config")
    return KafkaPublisher(
        topic=str(kwargs["topic"]),
        config=dict(config) if isinstance(config, dict) else None,
    )


__all__ = ["KafkaPublisher", "make_kafka_publisher"]
