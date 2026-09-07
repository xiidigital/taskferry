"""Taskport on Celery — enqueue Taskport tasks onto a Celery app.

```mermaid
flowchart LR
    APP["Application"]
    TP["Taskport"]
    AD["taskport_celery"]
    CEL["Celery"]
    BRK["broker (Redis / RabbitMQ)"]

    APP --> TP --> AD --> CEL --> BRK
```

Celery already solves the durable broker, the worker, prefetch and retries.
Taskport does not reimplement any of it: this adapter translates a
:class:`~taskport.specs.TaskSpec` into a Celery task and Celery's state back.
``taskport`` itself never imports ``celery`` — an architectural test enforces it —
so Celery becomes a dependency of your deployment only when this adapter is
selected.

Two sides
---------

**Submitting** (web process, CLI, anything)::

    from taskport import Taskport

    runtime = Taskport.from_mapping({
        "backends": {"cel": {"factory": "celery", "app": "myapp.celery:app"}},
        "defaults": {"task": "cel"},
    })
    runtime.tasks.submit("myapp.tasks:refresh", 42, queue="metadata")

**Executing** (worker process) — register the dispatcher once on your app::

    from celery import Celery
    from taskport_celery import register_dispatcher

    app = Celery("myapp", broker="redis://localhost:6379/0")
    register_dispatcher(app)

then run Celery's own worker (``celery -A myapp worker``). Taskport supplies no
worker; that is exactly the sort of thing it delegates.

What crosses the wire is the portable envelope — a ``"package.module:function"``
name plus JSON arguments — never a pickled callable. See :mod:`taskport.envelope`.
"""

from __future__ import annotations

from .backend import (
    CELERY_CAPABILITIES,
    DEFAULT_DISPATCH_TASK,
    CeleryTaskBackend,
    make_backend,
    map_state,
)
from .worker import build_message, execute_message, register_dispatcher

__version__ = "0.1.0"

__all__ = [
    "CELERY_CAPABILITIES",
    "DEFAULT_DISPATCH_TASK",
    "CeleryTaskBackend",
    "__version__",
    "build_message",
    "execute_message",
    "make_backend",
    "map_state",
    "register_dispatcher",
]
