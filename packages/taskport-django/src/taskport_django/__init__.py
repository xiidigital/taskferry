"""Taskport for Django — a bridge, in the direction Django expects.

```mermaid
sequenceDiagram
    participant App as Django app
    participant DT as django.tasks
    participant B as TaskportBackend
    participant TP as Taskport runtime
    participant E as engine

    App->>DT: my_task.enqueue(42)
    DT->>B: enqueue(task, args, kwargs)
    B->>TP: submit(TaskSpec)
    TP->>E: Procrastinate / Cloud Tasks / thread pool
    E-->>TP: execution
    TP-->>B: ExecutionHandle
    B-->>DT: TaskResult
```

Django 6 ships a real task API. Taskport does not compete with it and does not
add a second one — it supplies a **backend** for the standard ``TASKS`` setting,
so application code stays ordinary Django:

    # myapp/tasks.py — unchanged, forever
    from django.tasks import task

    @task
    def resize_image(image_id: int) -> None: ...

    # settings.py — this is the only thing that changes engines
    TASKS = {"default": {"BACKEND": "taskport_django.TaskportBackend"}}

    TASKPORT = {
        "backends": {"pg": {"factory": "procrastinate", "app": "myapp.tasks:app"}},
        "defaults": {"task": "pg"},
    }

The dependency direction is the whole point:

```mermaid
flowchart BT
    DJ["Django"]
    TPD["taskport_django"]
    TP["taskport"]

    DJ --> TPD --> TP
```

``taskport`` never imports Django. An architectural test enforces it, and
``pytest packages/taskport`` passes in an environment where Django is not
installed. So the same Procrastinate configuration serves a Django site, a
FastAPI service, a CLI and a library — none of which have to agree on a web
framework to agree on a queue.

What this package adds beyond the backend: configuration from ``settings.TASKPORT``
(:mod:`taskport_django.config`), enqueue-after-commit
(:mod:`taskport_django.transaction`), system checks that catch a misconfiguration
at ``manage.py check`` time rather than at 3am (:mod:`taskport_django.checks`), and
a ``manage.py taskport`` command wrapping the same CLI.
"""

from __future__ import annotations

from .backend import TaskportBackend
from .config import config_from_settings, get_runtime, reset_runtime
from .execute import run_task
from .transaction import submit_on_commit, task_on_commit
from .views import make_task_webhook, task_webhook

__version__ = "0.2.0"

__all__ = [
    "TaskportBackend",
    "__version__",
    "config_from_settings",
    "get_runtime",
    "make_task_webhook",
    "reset_runtime",
    "run_task",
    "submit_on_commit",
    "task_on_commit",
    "task_webhook",
]

default_app_config = "taskport_django.apps.TaskportConfig"
