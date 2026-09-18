"""The EventPublisher port and a base class applying cross-cutting rules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from typing import Protocol, runtime_checkable

from taskferry.core import (
    ATTR_EVENT_ID,
    ATTR_PROVIDER,
    SPAN_EVENT_PUBLISH,
    CapabilitySet,
    span,
)

from .models import Event, PublishResult


@runtime_checkable
class EventPublisher(Protocol):
    """Port for publishing events. Adapters implement this contract."""

    @property
    def provider(self) -> str: ...

    @property
    def capabilities(self) -> CapabilitySet: ...

    def publish(self, event: Event) -> PublishResult: ...

    def publish_batch(self, events: Iterable[Event]) -> list[PublishResult]: ...


class BaseEventPublisher(ABC):
    """Template base adding tracing; subclasses implement ``_publish``."""

    @property
    @abstractmethod
    def provider(self) -> str: ...

    @property
    @abstractmethod
    def capabilities(self) -> CapabilitySet: ...

    @abstractmethod
    def _publish(self, event: Event) -> PublishResult: ...

    def publish(self, event: Event) -> PublishResult:
        with span(
            SPAN_EVENT_PUBLISH,
            {ATTR_PROVIDER: self.provider, ATTR_EVENT_ID: event.id},
        ):
            return self._publish(event)

    def publish_batch(self, events: Iterable[Event]) -> list[PublishResult]:
        materialized: Sequence[Event] = list(events)
        return [self.publish(event) for event in materialized]


__all__ = ["BaseEventPublisher", "EventPublisher"]
