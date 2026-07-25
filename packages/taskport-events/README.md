# taskport-events

Portable **pub/sub and fan-out** event delivery. Part of the
[Taskport](https://taskport.dev) family. Plain Python, no Django required.

An **Event** declares that *something happened* and may have **zero or more**
consumers — the opposite of a Task, which is one operation to run. Modeled on
[CloudEvents](https://cloudevents.io/).

```bash
pip install taskport-events            # in-memory bus
pip install taskport-events[gcp]       # + Google Cloud Pub/Sub
```

## Usage

```python
from taskport.events import Event, publishers

publishers["default"].publish(
    Event(type="file.uploaded", source="urn:svc:uploads", data={"file_id": "42"})
)
```

Fan-out with the in-process bus:

```python
from taskport.events import InMemoryEventBus, Event

bus = InMemoryEventBus()
bus.subscribe(lambda e: print("billing", e.data), event_type="order.placed")
bus.subscribe(lambda e: print("analytics", e.data))  # sees everything
bus.publish(Event(type="order.placed", source="urn:shop", data={"total": 10}))
```

Configure a provider publisher (lazy — no SDK imported until first use):

```python
from taskport.events import publishers

publishers.configure(
    {
        "default": {
            "factory": "taskport.events.adapters.gcp:make_pubsub_publisher",
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
| `InMemoryEventBus` | inmemory  | fanout, filtering, replay, retention                |
| `PubSubPublisher`  | gcp       | fanout, delivery_retry, dead_letter, filtering, retention (+ ordering) |

Roadmap: EventBridge/SNS (AWS), Event Grid (Azure), Kafka, NATS, RabbitMQ.

## Delivery semantics

At-least-once with possible duplicates (ADR-0008). Consumers must be idempotent.

## License

Apache-2.0.
