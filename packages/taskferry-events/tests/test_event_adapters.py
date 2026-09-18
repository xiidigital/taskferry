"""Tests for the AWS/Azure/Kafka event adapters with injected SDK fakes."""

from __future__ import annotations

import json

from taskferry_events import Event, EventCapability
from taskferry_events.adapters.aws import EventBridgePublisher, SnsPublisher
from taskferry_events.adapters.azure import EventGridPublisher
from taskferry_events.adapters.kafka import KafkaPublisher
from taskferry_events.contract import EventPublisherContract


# --------------------------------------------------------------------------- #
# SNS
# --------------------------------------------------------------------------- #
class _FakeSns:
    def __init__(self) -> None:
        self.published: list[dict] = []

    def publish(self, **kwargs: object) -> dict:
        self.published.append(kwargs)
        return {"MessageId": "sns-1"}


def test_sns_publish_builds_message_and_attributes() -> None:
    client = _FakeSns()
    pub = SnsPublisher(topic_arn="arn:aws:sns:us-east-1:1:topic", client=client)
    event = Event(type="order.placed", source="urn:shop", data={"id": "1"}, subject="o-1")
    result = pub.publish(event)
    assert result.provider_metadata.provider_id == "sns-1"
    sent = client.published[0]
    assert json.loads(sent["Message"])["type"] == "order.placed"
    assert sent["MessageAttributes"]["taskferry-event-type"]["StringValue"] == "order.placed"


def test_sns_fifo_sets_group_and_ordering_capability() -> None:
    client = _FakeSns()
    pub = SnsPublisher(topic_arn="arn:aws:sns:us-east-1:1:topic.fifo", client=client)
    assert EventCapability.ORDERING in pub.capabilities
    pub.publish(Event(type="t", source="s", subject="k"))
    assert client.published[0]["MessageGroupId"] == "k"


class TestSnsContract(EventPublisherContract):
    def make_publisher(self) -> SnsPublisher:
        return SnsPublisher(topic_arn="arn:aws:sns:us-east-1:1:t", client=_FakeSns())


# --------------------------------------------------------------------------- #
# EventBridge
# --------------------------------------------------------------------------- #
class _FakeEvents:
    def __init__(self, failed: int = 0) -> None:
        self.entries: list[dict] = []
        self._failed = failed

    def put_events(self, Entries: list[dict]) -> dict:
        self.entries.extend(Entries)
        return {"FailedEntryCount": self._failed, "Entries": [{"EventId": "eb-1"}]}


def test_eventbridge_put_events() -> None:
    client = _FakeEvents()
    pub = EventBridgePublisher(event_bus_name="bus", client=client)
    result = pub.publish(Event(type="dataset.updated", source="urn:svc", data={"id": "9"}))
    assert result.provider_metadata.provider_id == "eb-1"
    entry = client.entries[0]
    assert entry["DetailType"] == "dataset.updated"
    assert json.loads(entry["Detail"]) == {"id": "9"}
    assert entry["EventBusName"] == "bus"


def test_eventbridge_raises_on_failed_entries() -> None:
    import pytest

    from taskferry.core import ProviderError

    pub = EventBridgePublisher(client=_FakeEvents(failed=1))
    with pytest.raises(ProviderError):
        pub.publish(Event(type="t", source="s"))


class TestEventBridgeContract(EventPublisherContract):
    def make_publisher(self) -> EventBridgePublisher:
        return EventBridgePublisher(client=_FakeEvents())


# --------------------------------------------------------------------------- #
# Event Grid
# --------------------------------------------------------------------------- #
class _FakeEventGrid:
    def __init__(self) -> None:
        self.sent: list[list] = []

    def send(self, events: list) -> None:
        self.sent.append(events)


def test_eventgrid_sends_cloudevents() -> None:
    client = _FakeEventGrid()
    pub = EventGridPublisher(endpoint="https://topic.eventgrid.azure.net", client=client)
    event = Event(type="file.uploaded", source="urn:up", data={"f": "1"})
    result = pub.publish(event)
    assert result.event_id == event.id
    assert client.sent[0][0]["type"] == "file.uploaded"


class TestEventGridContract(EventPublisherContract):
    def make_publisher(self) -> EventGridPublisher:
        return EventGridPublisher(endpoint="https://x.eventgrid.azure.net", client=_FakeEventGrid())


# --------------------------------------------------------------------------- #
# Kafka
# --------------------------------------------------------------------------- #
class _FakeProducer:
    def __init__(self) -> None:
        self.produced: list[dict] = []
        self.flushed = 0

    def produce(self, topic: str, value: bytes, key: bytes, headers: list) -> None:
        self.produced.append({"topic": topic, "value": value, "key": key, "headers": headers})

    def flush(self, *args: object) -> None:
        self.flushed += 1


def test_kafka_produce_uses_subject_as_key() -> None:
    producer = _FakeProducer()
    pub = KafkaPublisher(topic="events", producer=producer)
    assert EventCapability.ORDERING in pub.capabilities
    pub.publish(Event(type="x", source="s", data={"a": 1}, subject="key-1"))
    msg = producer.produced[0]
    assert msg["topic"] == "events"
    assert msg["key"] == b"key-1"
    assert json.loads(msg["value"])["type"] == "x"
    assert producer.flushed == 1


class TestKafkaContract(EventPublisherContract):
    def make_publisher(self) -> KafkaPublisher:
        return KafkaPublisher(topic="t", producer=_FakeProducer())
