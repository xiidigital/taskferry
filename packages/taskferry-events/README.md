# taskferry-events

Portable **pub/sub and fan-out** event delivery. Ships alongside [Taskferry](https://github.com/xiidigital/taskferry),
the portable execution layer. Plain Python, no Django required.

An **Event** declares that *something happened* and may have **zero or more**
consumers — the opposite of a Task, which is one operation to run. Modeled on
[CloudEvents](https://cloudevents.io/).

```bash
pip install taskferry-events            # in-memory bus
pip install taskferry-events[gcp]       # + Google Cloud Pub/Sub
```

## Usage

```python
from taskferry_events import Event, publishers

publishers["default"].publish(
    Event(type="file.uploaded", source="urn:svc:uploads", data={"file_id": "42"})
)
```

Fan-out with the in-process bus:

```python
from taskferry_events import InMemoryEventBus, Event

bus = InMemoryEventBus()
bus.subscribe(lambda e: print("billing", e.data), event_type="order.placed")
bus.subscribe(lambda e: print("analytics", e.data))  # sees everything
bus.publish(Event(type="order.placed", source="urn:shop", data={"total": 10}))
```

Configure a provider publisher (lazy — no SDK imported until first use):

```python
from taskferry_events import publishers

publishers.configure(
    {
        "default": {
            "factory": "taskferry_events.adapters.gcp:make_pubsub_publisher",
            "project": "my-proj",
            "topic": "domain-events",
            "ordering": True,
        },
    }
)
```

## Task vs Event

```text
Task  → one operation to execute       (~1 consumer)
Event → a fact that happened           (0..N consumers, fan-out)
```

## Capabilities

Adapters advertise what they support (`fanout`, `ordering`, `delivery_retry`,
`dead_letter`, `filtering`, `replay`, `retention`). Requesting `replay` on a
backend that lacks it raises `UnsupportedCapabilityError`.

| Adapter            | Provider  | Capabilities                                        |
| ------------------ | --------- | --------------------------------------------------- |
| `InMemoryEventBus`      | inmemory  | fanout, filtering, replay, retention           |
| `PubSubPublisher`       | gcp       | fanout, delivery_retry, dead_letter, filtering, retention (+ ordering) |
| `SnsPublisher`          | aws       | fanout, delivery_retry, dead_letter, filtering (+ ordering on FIFO) |
| `EventBridgePublisher`  | aws       | fanout, delivery_retry, dead_letter, filtering, retention, replay |
| `EventGridPublisher`    | azure     | fanout, delivery_retry, dead_letter, filtering, retention |
| `KafkaPublisher`        | kafka     | fanout, ordering, delivery_retry, retention, replay |

Roadmap: NATS, RabbitMQ.

## Delivery semantics

At-least-once with possible duplicates (ADR-0010). Consumers must be idempotent.

## License

Apache-2.0.
