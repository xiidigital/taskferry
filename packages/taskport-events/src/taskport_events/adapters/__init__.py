"""Event backend adapters. Provider SDKs are imported lazily (section 31)."""

from __future__ import annotations

from .aws import (
    EventBridgePublisher,
    SnsPublisher,
    make_eventbridge_publisher,
    make_sns_publisher,
)
from .azure import EventGridPublisher, make_eventgrid_publisher
from .gcp import PubSubPublisher, make_pubsub_publisher
from .inmemory import (
    InMemoryEventBus,
    Subscription,
    make_in_memory_event_bus,
)
from .kafka import KafkaPublisher, make_kafka_publisher

__all__ = [
    "EventBridgePublisher",
    "EventGridPublisher",
    "InMemoryEventBus",
    "KafkaPublisher",
    "PubSubPublisher",
    "SnsPublisher",
    "Subscription",
    "make_eventbridge_publisher",
    "make_eventgrid_publisher",
    "make_in_memory_event_bus",
    "make_kafka_publisher",
    "make_pubsub_publisher",
    "make_sns_publisher",
]
