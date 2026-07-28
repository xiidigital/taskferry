"""InMemoryEventBus — a real in-process pub/sub bus.

Provides genuine fan-out (0..N consumers), type/predicate filtering, replay of
retained history, and retention — enough to build and test event-driven flows in
a single process and to serve as the reference the contract tests run against.
Not durable and not cross-process: use a provider adapter for that.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from taskport.core import CapabilitySet, ProviderMetadata, new_id

from ..capabilities import EventCapability
from ..models import Event, PublishResult
from ..publisher import BaseEventPublisher

EventHandler = Callable[[Event], None]
EventPredicate = Callable[[Event], bool]

_INMEMORY_CAPABILITIES = frozenset(
    {
        EventCapability.FANOUT,
        EventCapability.FILTERING,
        EventCapability.REPLAY,
        EventCapability.RETENTION,
    }
)


@dataclass
class Subscription:
    """A live subscription; call :meth:`unsubscribe` to remove it."""

    id: str
    handler: EventHandler
    event_type: str | None
    predicate: EventPredicate | None
    _bus: InMemoryEventBus = field(repr=False)

    def matches(self, event: Event) -> bool:
        if self.event_type is not None and event.type != self.event_type:
            return False
        return self.predicate is None or self.predicate(event)

    def unsubscribe(self) -> None:
        self._bus._remove(self)


class InMemoryEventBus(BaseEventPublisher):
    """In-process event bus with fan-out delivery and replayable history."""

    def __init__(self, *, retain: bool = True, max_history: int = 1000) -> None:
        self._subscriptions: list[Subscription] = []
        self._history: list[Event] = []
        self._retain = retain
        self._max_history = max_history
        self._lock = threading.Lock()

    @property
    def provider(self) -> str:
        return "inmemory"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(_INMEMORY_CAPABILITIES, provider="inmemory")

    # -- pub/sub ------------------------------------------------------------ #
    def subscribe(
        self,
        handler: EventHandler,
        *,
        event_type: str | None = None,
        predicate: EventPredicate | None = None,
    ) -> Subscription:
        subscription = Subscription(
            id=new_id("sub"),
            handler=handler,
            event_type=event_type,
            predicate=predicate,
            _bus=self,
        )
        with self._lock:
            self._subscriptions.append(subscription)
        return subscription

    def _remove(self, subscription: Subscription) -> None:
        with self._lock:
            if subscription in self._subscriptions:
                self._subscriptions.remove(subscription)

    def _publish(self, event: Event) -> PublishResult:
        with self._lock:
            if self._retain:
                self._history.append(event)
                if len(self._history) > self._max_history:
                    self._history = self._history[-self._max_history :]
            targets = [s for s in self._subscriptions if s.matches(event)]
        # Deliver outside the lock so handlers may (un)subscribe safely.
        for subscription in targets:
            subscription.handler(event)
        metadata = ProviderMetadata(provider="inmemory", provider_id=event.id)
        return PublishResult(event_id=event.id, provider_metadata=metadata)

    # -- replay / retention ------------------------------------------------- #
    @property
    def history(self) -> tuple[Event, ...]:
        with self._lock:
            return tuple(self._history)

    def replay(self, handler: EventHandler, *, event_type: str | None = None) -> int:
        """Replay retained events to ``handler``; returns how many were delivered."""
        self.capabilities.require(EventCapability.REPLAY)
        delivered = 0
        for event in self.history:
            if event_type is None or event.type == event_type:
                handler(event)
                delivered += 1
        return delivered

    def clear(self) -> None:
        with self._lock:
            self._history.clear()


def make_in_memory_event_bus(**kwargs: object) -> InMemoryEventBus:
    max_history = kwargs.get("max_history", 1000)
    return InMemoryEventBus(
        retain=bool(kwargs.get("retain", True)),
        max_history=int(max_history),  # type: ignore[call-overload]
    )


__all__ = [
    "EventHandler",
    "EventPredicate",
    "InMemoryEventBus",
    "Subscription",
    "make_in_memory_event_bus",
]
