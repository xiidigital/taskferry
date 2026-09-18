"""Fan-out pub/sub with the in-memory bus: many consumers, filtering.

uv run python examples/events-inmemory/run.py
"""

from __future__ import annotations

from taskferry_events import Event, InMemoryEventBus


def main() -> None:
    bus = InMemoryEventBus()

    # Three independent consumers of the same fact (fan-out).
    bus.subscribe(lambda e: print("billing    ->", e.data), event_type="order.placed")
    bus.subscribe(lambda e: print("inventory  ->", e.data), event_type="order.placed")
    bus.subscribe(lambda e: print("analytics  ->", e.type))  # sees everything

    bus.publish(Event(type="order.placed", source="urn:shop", data={"order_id": "A1"}))
    bus.publish(Event(type="order.cancelled", source="urn:shop", data={"order_id": "A1"}))

    print("history retained:", len(bus.history), "events")


if __name__ == "__main__":
    main()
