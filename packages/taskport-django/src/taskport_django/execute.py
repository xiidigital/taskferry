"""The worker-side dispatcher for Django tasks.

```mermaid
flowchart LR
    SPEC["TaskSpec<br/>task = execute:run_task<br/>args = (module_path, args, kwargs)"]
    ENG["any engine"]
    RUN["run_task()"]
    T["django.tasks Task"]
    FN["your function"]

    SPEC --> ENG --> RUN --> T --> FN
```

Why a dispatcher rather than the task's own path
------------------------------------------------

``@task`` replaces the decorated function with a ``django.tasks.base.Task``
object, which is *not* callable — it exposes ``.call()`` and keeps the original
function in ``.func``. So ``myapp.tasks:resize_image`` resolves to the Task, and
a generic worker calling it would fail.

Taskport could special-case that, but only by teaching the core what a Django
Task is, which is precisely the coupling this project exists to prevent. Instead
the bridge does what the Procrastinate adapter already does for its own reasons:
route every Django task through one fixed entry point that knows how to run one.

Two useful consequences:

* **Allowlisting is trivial.** Only ``taskport_django.execute`` needs to be
  importable by the worker. The Django task path is data inside the payload,
  resolved through Django's own registry, not through an arbitrary import.
* **Django's semantics are preserved.** ``Task.call()`` is what Django's own
  backends invoke, so signature handling, context and validation behave exactly
  as they do under ``ImmediateBackend``.

The cost is that ``spec.task`` is the same string for every Django task, so
:attr:`~taskport.specs.ExecutionSpec.name` carries the real task name and routing
by ``name`` keeps working. That is stated here rather than discovered later.
"""

from __future__ import annotations

import importlib
from typing import Any

from django.tasks.base import Task

from taskport.core.typing import JSONValue
from taskport.errors import FunctionResolutionError


def resolve_django_task(module_path: str) -> Task:
    """Resolve ``"myapp.tasks.resize_image"`` to the ``Task`` object.

    Raises:
        FunctionResolutionError: when the path does not resolve to a Django Task.
            Being strict matters: this runs on a worker, on a payload that came
            off a queue, and "import whatever this string says and call it" is
            not something to do casually.
    """
    module_name, _, attribute = module_path.rpartition(".")
    if not module_name or not attribute:
        raise FunctionResolutionError(
            f"{module_path!r} is not a Django task path; expected 'package.module.task_name'"
        )
    try:
        module = importlib.import_module(module_name)
        candidate = getattr(module, attribute)
    except (ImportError, AttributeError) as exc:
        raise FunctionResolutionError(f"cannot resolve Django task {module_path!r}: {exc}") from exc
    if not isinstance(candidate, Task):
        raise FunctionResolutionError(
            f"{module_path!r} resolved to {type(candidate).__name__}, not a django.tasks Task"
        )
    return candidate


def run_task(
    module_path: str,
    args: list[JSONValue] | None = None,
    kwargs: dict[str, JSONValue] | None = None,
) -> Any:
    """Run a Django task by path. The single entry point every engine calls.

    Args:
        module_path: Django's own ``Task.module_path``.
        args: Positional arguments, JSON-shaped.
        kwargs: Keyword arguments, JSON-shaped.

    Returns:
        Whatever the task returned. Most engines discard it; backends that
        advertise ``RESULT`` surface it through the handle.
    """
    task = resolve_django_task(module_path)
    return task.call(*(args or []), **(kwargs or {}))


DISPATCH_REF = "taskport_django.execute:run_task"
"""The portable reference every Django-originated `TaskSpec` points at."""


__all__ = ["DISPATCH_REF", "resolve_django_task", "run_task"]
