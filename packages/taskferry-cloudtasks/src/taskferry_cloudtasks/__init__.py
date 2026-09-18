"""Taskferry on Google Cloud Tasks — push-based, serverless task execution.

```mermaid
sequenceDiagram
    participant App
    participant TP as Taskferry
    participant AD as taskferry_cloudtasks
    participant CT as Cloud Tasks
    participant SVC as your HTTP service

    App->>TP: tasks.submit("myapp:send_email", 42)
    TP->>AD: TaskSpec
    AD->>CT: create_task(http_request)
    CT->>SVC: POST /_taskferry/execute
    SVC->>AD: handle_request(body)
    AD->>SVC: run the function
```

The second task engine, and the reason the first one had to be an adapter. Cloud
Tasks is architecturally the opposite of Procrastinate — nothing polls, no worker
process exists, Google pushes an HTTP request at your service and scale-to-zero
is the normal state — and the *application code does not change*:

    runtime.tasks.submit("myapp.tasks:send_email", 42, queue="email")

The queue moves from PostgreSQL to Cloud Tasks by editing configuration. That is
the entire promise of a portability layer, and this package is where it either
holds or does not.

What genuinely differs, and is therefore visible in the capabilities: Cloud Tasks
does not report per-task state after creation and stores no results, so ``STATE``
and ``RESULT`` are not advertised and a handle refuses to fake them. It does
support scheduling and native retries, and those are advertised.

The receiving side is framework-agnostic — :func:`handle_request` takes bytes and
returns whatever the task returned, so it plugs into Django, FastAPI, Flask or a
bare WSGI app in four lines. See :mod:`taskferry_cloudtasks.receiver`.
"""

from __future__ import annotations

from .backend import CLOUD_TASKS_CAPABILITIES, CloudTasksBackend, make_backend
from .receiver import handle_request, parse_request

__version__ = "0.2.0"

__all__ = [
    "CLOUD_TASKS_CAPABILITIES",
    "CloudTasksBackend",
    "__version__",
    "handle_request",
    "make_backend",
    "parse_request",
]
