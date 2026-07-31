"""Taskport on AWS SQS — pull-based task delivery.

```mermaid
flowchart LR
    APP["Application"]
    TP["Taskport"]
    AD["taskport_sqs"]
    Q["SQS queue"]
    C["consumer<br/>Lambda · ECS · Fargate"]

    APP --> TP --> AD --> Q --> C
```

SQS is the third shape of task engine Taskport adapts, and the differences from
the other two are exactly what the capability model exists to express:

* **Procrastinate** — a worker polls PostgreSQL. Reports state, cancels jobs.
* **Cloud Tasks** — Google pushes HTTP at your service. Reports nothing.
* **SQS** — *you* poll, from a Lambda or a container. Reports nothing per message,
  but has genuine FIFO ordering and a real dead-letter queue.

The application code is identical across all three:

    runtime.tasks.submit("myapp.tasks:send_email", 42, queue="email")

Both sides
----------

**Sending**::

    runtime = Taskport.from_mapping({
        "backends": {"sqs": {
            "factory": "sqs",
            "queue_url": "https://sqs.eu-west-1.amazonaws.com/123/tasks",
        }},
        "defaults": {"task": "sqs"},
    })

**Consuming**, from an AWS Lambda handler::

    from taskport import FunctionRegistry
    from taskport_sqs import process_event

    REGISTRY = FunctionRegistry(allowed_modules=["myapp"])

    def handler(event, context):
        return process_event(event, registry=REGISTRY)

Taskport supplies no worker daemon here either — a Lambda, an ECS service or a
simple polling loop you already run is the consumer, and SQS handles visibility
timeouts and redrive. See :mod:`taskport_sqs.consumer`.
"""

from __future__ import annotations

from .backend import MAX_DELAY_SECONDS, SQS_CAPABILITIES, SQSTaskBackend, make_backend
from .consumer import poll_forever, process_event, process_message

__version__ = "0.2.0"

__all__ = [
    "MAX_DELAY_SECONDS",
    "SQS_CAPABILITIES",
    "SQSTaskBackend",
    "__version__",
    "make_backend",
    "poll_forever",
    "process_event",
    "process_message",
]
