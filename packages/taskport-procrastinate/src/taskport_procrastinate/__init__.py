"""Taskport on Procrastinate — a PostgreSQL-backed task engine.

```mermaid
flowchart LR
    APP["Application"]
    TP["Taskport"]
    AD["taskport_procrastinate"]
    PRO["Procrastinate"]
    PG["PostgreSQL"]

    APP --> TP --> AD --> PRO --> PG
```

Procrastinate already solves the hard parts — a durable PostgreSQL queue, worker
processes, job reservation with `FOR UPDATE SKIP LOCKED`, retries, periodic
tasks. Taskport does not reimplement any of that and does not want to. This
adapter translates a :class:`~taskport.specs.TaskSpec` into a deferred
Procrastinate job and translates Procrastinate's states back.

PostgreSQL becomes a dependency of your deployment only when this adapter is
selected. ``taskport`` itself never imports ``procrastinate`` or a database
driver, and an architectural test enforces that.

Two sides
---------

**Submitting** (web process, CLI, anything)::

    from taskport import Taskport

    runtime = Taskport.from_mapping({
        "backends": {"pg": {"factory": "procrastinate", "app": "myapp.tasks:app"}},
        "defaults": {"task": "pg"},
    })
    runtime.tasks.submit("myapp.tasks:refresh_metadata", 42, queue="metadata")

**Executing** (worker process) — register the dispatcher once on your app::

    from procrastinate import App, PsycopgConnector
    from taskport_procrastinate import register_dispatcher

    app = App(connector=PsycopgConnector(...))
    register_dispatcher(app)

then run Procrastinate's own worker (``procrastinate worker``). Taskport does not
supply a worker: that is exactly the sort of thing it delegates.

What crosses the wire is the portable message — a ``"package.module:function"``
name plus JSON arguments — never a pickled callable. See
:mod:`taskport.functions`.
"""

from __future__ import annotations

from .backend import (
    DEFAULT_DISPATCH_TASK,
    ProcrastinateTaskBackend,
    make_backend,
    map_status,
)
from .worker import build_message, execute_message, register_dispatcher

__version__ = "0.2.0"

__all__ = [
    "DEFAULT_DISPATCH_TASK",
    "ProcrastinateTaskBackend",
    "__version__",
    "build_message",
    "execute_message",
    "make_backend",
    "map_status",
    "register_dispatcher",
]
