"""Tests for taskport.events: model, in-memory bus, Pub/Sub adapter, contract."""

from __future__ import annotations

import json

import pytest

from taskport.core import Correlation, SerializationError
from taskport.events import (
    Event,
    EventCapability,
    InMemoryEventBus,
    publishers,
)
from taskport.events.adapters.gcp import PubSubPublisher
from taskport.events.contract import EventPublisherContract


# --------------------------------------------------------------------------- #
# Event model
# --------------------------------------------------------------------------- #
def test_event_defaults_and_immutability() -> None:
    event = Event(type="resource.created", source="urn:svc", data={"id": "1"})
    assert event.id.startswith("evt_")
    assert event.time.tzinfo is not None
    with pytest.raises(TypeError):
        event.data["x"] = 1  # type: ignore[index]
    with pytest.raises(AttributeError):
        event.type = "x"  # type: ignore[misc]


def test_event_rejects_non_json_data() -> None:
    with pytest.raises(SerializationError):
        Event(type="t", source="s", data={"bad": {1, 2}})  # type: ignore[dict-item]


def test_event_to_cloudevent_is_json_safe() -> None:
    corr = Correlation.start()
    event = Event(
        type="file.uploaded", source="urn:up", data={"k": "v"}, subject="file-1", correlation=corr
    )
    envelope = event.to_cloudevent()
    dumped = json.loads(json.dumps(envelope))
    assert dumped["type"] == "file.uploaded"
    assert dumped["subject"] == "file-1"
    assert dumped["specversion"] == "1.0"
    assert dumped["taskportcorrelation"]["taskport-correlation-id"] == corr.correlation_id


# --------------------------------------------------------------------------- #
# InMemoryEventBus
# --------------------------------------------------------------------------- #
def test_default_publisher_is_in_memory_bus() -> None:
    assert publishers["default"].provider == "inmemory"


def test_bus_fans_out_to_multiple_subscribers() -> None:
    bus = InMemoryEventBus()
    seen_a: list[str] = []
    seen_b: list[str] = []
    bus.subscribe(lambda e: seen_a.append(e.type))
    bus.subscribe(lambda e: seen_b.append(e.type))
    bus.publish(Event(type="x.happened", source="s"))
    assert seen_a == ["x.happened"]
    assert seen_b == ["x.happened"]  # fan-out: both received it


def test_bus_filters_by_type_and_predicate() -> None:
    bus = InMemoryEventBus()
    typed: list[Event] = []
    filtered: list[Event] = []
    bus.subscribe(typed.append, event_type="order.placed")
    bus.subscribe(filtered.append, predicate=lambda e: e.data.get("vip") is True)

    bus.publish(Event(type="order.placed", source="s", data={"vip": True}))
    bus.publish(Event(type="order.cancelled", source="s", data={"vip": False}))

    assert [e.type for e in typed] == ["order.placed"]
    assert [e.data["vip"] for e in filtered] == [True]


def test_bus_unsubscribe_stops_delivery() -> None:
    bus = InMemoryEventBus()
    received: list[Event] = []
    sub = bus.subscribe(received.append)
    bus.publish(Event(type="a", source="s"))
    sub.unsubscribe()
    bus.publish(Event(type="b", source="s"))
    assert [e.type for e in received] == ["a"]


def test_bus_replay_and_retention() -> None:
    bus = InMemoryEventBus()
    bus.publish(Event(type="a", source="s"))
    bus.publish(Event(type="b", source="s"))
    assert len(bus.history) == 2
    replayed: list[str] = []
    count = bus.replay(lambda e: replayed.append(e.type))
    assert count == 2
    assert replayed == ["a", "b"]
    only_a: list[str] = []
    assert bus.replay(lambda e: only_a.append(e.type), event_type="a") == 1


def test_bus_capabilities() -> None:
    caps = InMemoryEventBus().capabilities
    assert EventCapability.FANOUT in caps
    assert EventCapability.REPLAY in caps
    assert EventCapability.ORDERING not in caps


class TestInMemoryContract(EventPublisherContract):
    def make_publisher(self) -> InMemoryEventBus:
        return InMemoryEventBus()


# --------------------------------------------------------------------------- #
# GCP Pub/Sub adapter (fake client)
# --------------------------------------------------------------------------- #
class _FakeFuture:
    def __init__(self, message_id: str) -> None:
        self._message_id = message_id

    def result(self) -> str:
        return self._message_id


class _FakePublisherClient:
    def __init__(self) -> None:
        self.published: list[tuple] = []

    def topic_path(self, project: str, topic: str) -> str:
        return f"projects/{project}/topics/{topic}"

    def publish(self, topic_path: str, data: bytes, **attributes: str) -> _FakeFuture:
        self.published.append((topic_path, data, attributes))
        return _FakeFuture("msg-1")


def test_pubsub_publish_builds_message() -> None:
    client = _FakePublisherClient()
    publisher = PubSubPublisher(project="p", topic="events", client=client)
    event = Event(type="dataset.updated", source="urn:svc", data={"id": "9"}, subject="ds-9")
    result = publisher.publish(event)
    assert result.provider_metadata.provider_id == "msg-1"
    topic_path, data, attributes = client.published[0]
    assert topic_path == "projects/p/topics/events"
    assert json.loads(data)["type"] == "dataset.updated"
    assert attributes["taskport-event-type"] == "dataset.updated"
    assert attributes["taskport-event-subject"] == "ds-9"


def test_pubsub_ordering_sets_key_and_capability() -> None:
    client = _FakePublisherClient()
    publisher = PubSubPublisher(project="p", topic="t", client=client, ordering=True)
    assert EventCapability.ORDERING in publisher.capabilities
    publisher.publish(Event(type="t", source="s", data={}, subject="key-1"))
    _, _, attributes = client.published[0]
    assert attributes["ordering_key"] == "key-1"


class TestPubSubContract(EventPublisherContract):
    def make_publisher(self) -> PubSubPublisher:
        return PubSubPublisher(project="p", topic="t", client=_FakePublisherClient())
