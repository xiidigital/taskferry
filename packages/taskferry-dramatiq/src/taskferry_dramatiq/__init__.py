"""Taskferry on Dramatiq — Redis or RabbitMQ backed tasks with real workers.

```mermaid
flowchart LR
    APP["Application"]
    TP["Taskferry"]
    AD["taskferry_dramatiq"]
    DQ["Dramatiq"]
    B["Redis / RabbitMQ"]
    W["dramatiq worker"]

    APP --> TP --> AD --> DQ --> B --> W
```

Dramatiq is the Redis-shaped counterpart to Procrastinate's PostgreSQL: a real
broker, real worker processes, real retries with exponential backoff, and a
dead-letter queue. Taskferry reimplements none of it.

The pairing matters for the design. Procrastinate and Dramatiq are the two
worker-based engines Taskferry adapts, and they differ in ways the capability
model has to express honestly — Dramatiq has no per-message state lookup and no
cancel, where Procrastinate has both. Same application code, different truthful
answers.

**Sending**::

    runtime = Taskferry.from_mapping({
        "backends": {"dq": {"factory": "dramatiq", "actor": "myapp.worker:taskferry_execute"}},
        "defaults": {"task": "dq"},
    })

**Executing** — build the actor once, in the module your worker loads::

    # myapp/worker.py
    import dramatiq
    from dramatiq.brokers.redis import RedisBroker
    from taskferry import FunctionRegistry
    from taskferry_dramatiq import build_dispatch_actor

    dramatiq.set_broker(RedisBroker(url="redis://localhost:6379"))

    taskferry_execute = build_dispatch_actor(
        registry=FunctionRegistry(allowed_modules=["myapp"]),
        max_retries=3,
    )

```bash
dramatiq myapp.worker
```

Taskferry supplies no worker: ``dramatiq`` is the worker, and it is a good one.
"""

from __future__ import annotations

from .backend import (
    DEFAULT_ACTOR_NAME,
    DRAMATIQ_CAPABILITIES,
    DramatiqTaskBackend,
    make_backend,
)
from .worker import build_dispatch_actor, dispatch

__version__ = "0.2.0"

__all__ = [
    "DEFAULT_ACTOR_NAME",
    "DRAMATIQ_CAPABILITIES",
    "DramatiqTaskBackend",
    "__version__",
    "build_dispatch_actor",
    "dispatch",
    "make_backend",
]
