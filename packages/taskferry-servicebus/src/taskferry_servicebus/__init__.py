"""Taskferry on Azure Service Bus — pull-based tasks with real scheduling.

```mermaid
flowchart LR
    APP["Application"]
    TP["Taskferry"]
    AD["taskferry_servicebus"]
    Q["Service Bus queue"]
    C["consumer<br/>Azure Functions · Container Apps"]

    APP --> TP --> AD --> Q --> C
```

Service Bus is the pull-based engine that does **not** share SQS's fifteen-minute
delay ceiling: ``scheduled_enqueue_time_utc`` accepts any future time, so a task
deferred by a week is a first-class operation rather than something you need a
scheduler for.

That single difference is why the two adapters are separate packages with
different capability sets rather than one "queue adapter" with a flag. See
[ADR-0005](../../../docs/adr/0005-capability-model.md).

**Sending**::

    runtime = Taskferry.from_mapping({
        "backends": {"sb": {
            "factory": "servicebus",
            "connection_string": "Endpoint=sb://...",
            "queue_name": "tasks",
        }},
        "defaults": {"task": "sb"},
    })

**Consuming**, from an Azure Function::

    from taskferry import FunctionRegistry
    from taskferry_servicebus import process_message

    REGISTRY = FunctionRegistry(allowed_modules=["myapp"])

    def main(msg: func.ServiceBusMessage) -> None:
        process_message(msg.get_body(), registry=REGISTRY)

The SDK is imported lazily and both the client and the message factory are
injectable, so the test suite needs no Azure subscription.
"""

from __future__ import annotations

from .backend import SERVICE_BUS_CAPABILITIES, ServiceBusTaskBackend, make_backend
from .consumer import process_message, receive_forever

__version__ = "0.2.0"

__all__ = [
    "SERVICE_BUS_CAPABILITIES",
    "ServiceBusTaskBackend",
    "__version__",
    "make_backend",
    "process_message",
    "receive_forever",
]
