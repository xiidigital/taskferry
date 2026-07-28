"""Reusable contract test suite for :class:`EventPublisher` implementations.

Every publisher adapter must pass this contract (section 37). Requires ``pytest``.

    from taskport_events.contract import EventPublisherContract

    class TestInMemory(EventPublisherContract):
        def make_publisher(self):
            return InMemoryEventBus()
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from taskport.core import CapabilitySet

from .models import Event, PublishResult
from .publisher import EventPublisher


class EventPublisherContract(ABC):
    """Subclass and implement ``make_publisher``."""

    @abstractmethod
    def make_publisher(self) -> EventPublisher:
        """Return a fresh publisher under test."""

    def sample_event(self) -> Event:
        return Event(type="resource.created", source="urn:test", data={"id": "1"})

    def test_capabilities_are_a_set_for_provider(self) -> None:
        publisher = self.make_publisher()
        caps = publisher.capabilities
        assert isinstance(caps, CapabilitySet)
        assert caps.provider == publisher.provider

    def test_publish_returns_result_bound_to_event(self) -> None:
        publisher = self.make_publisher()
        event = self.sample_event()
        result = publisher.publish(event)
        assert isinstance(result, PublishResult)
        assert result.event_id == event.id
        assert result.provider == publisher.provider

    def test_publish_batch(self) -> None:
        publisher = self.make_publisher()
        events = [self.sample_event(), self.sample_event()]
        results = publisher.publish_batch(events)
        assert [r.event_id for r in results] == [e.id for e in events]


__all__ = ["EventPublisherContract"]
