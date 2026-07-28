"""System checks — find the misconfiguration at ``manage.py check``, not at 3am.

Every check here answers a question that otherwise gets answered by a production
incident:

* is ``TASKPORT`` even parseable?
* do the routes point at backends that exist?
* is every configured backend actually *constructible* — adapter installed,
  required options present, credentials resolvable?
* does anything have a default backend to fall back on?
* is ``TASKS`` pointed at this bridge at all?

The expensive one is deliberate. "The adapter is installed" and "the adapter can
be built with these options" are different questions, and only the second one
matters. Building each backend at check time surfaces a missing ``project`` or an
unresolvable Procrastinate app during deployment, where a failed check is cheap.

```mermaid
flowchart TD
    C["manage.py check"]
    S["settings.TASKPORT parses?"]
    R["routes name known backends?"]
    B["each backend constructible?"]
    D["defaults present?"]
    T["TASKS points at TaskportBackend?"]

    C --> S --> R --> B --> D --> T
```

Registered by :class:`taskport_django.apps.TaskportConfig`, so adding
``"taskport_django"`` to ``INSTALLED_APPS`` is all it takes.
"""

from __future__ import annotations

from typing import Any

from django.core.checks import CheckMessage, Error, Warning

from taskport.errors import TaskportError

ID_CONFIG = "taskport.E001"
ID_BACKEND = "taskport.E002"
ID_NO_DEFAULT = "taskport.W001"
ID_NOT_WIRED = "taskport.W002"
ID_LOCAL_IN_PRODUCTION = "taskport.W003"


def check_taskport(app_configs: Any = None, **kwargs: Any) -> list[CheckMessage]:
    """Validate the Taskport configuration. Registered as a Django system check."""
    from django.conf import settings

    from .config import config_from_settings

    messages: list[CheckMessage] = []

    try:
        config = config_from_settings(settings)
    except Exception as exc:
        return [
            Error(
                f"settings.TASKPORT could not be loaded: {exc}",
                hint="TASKPORT must be a dict of {'backends': ..., 'routes': ..., 'defaults': ...}",
                id=ID_CONFIG,
            )
        ]

    messages += _check_backends_build(config)
    messages += _check_defaults(config)
    messages += _check_tasks_setting(settings)
    messages += _check_not_local_in_production(settings, config)
    return messages


def _check_backends_build(config: Any) -> list[CheckMessage]:
    """Actually construct every configured backend."""
    from taskport import Taskport

    messages: list[CheckMessage] = []
    runtime = Taskport(config=config)
    try:
        for name in sorted(config.backends):
            try:
                runtime.backend(name)
            except TaskportError as exc:
                messages.append(
                    Error(
                        f"the Taskport backend {name!r} cannot be built: {exc}",
                        hint="Check that its adapter distribution is installed and that "
                        "every required option is set in settings.TASKPORT['backends'].",
                        id=ID_BACKEND,
                    )
                )
            except Exception as exc:  # an adapter raising something unexpected
                messages.append(
                    Error(
                        f"the Taskport backend {name!r} raised {type(exc).__name__}: {exc}",
                        id=ID_BACKEND,
                    )
                )
    finally:
        runtime.close()
    return messages


def _check_defaults(config: Any) -> list[CheckMessage]:
    from taskport import ExecutionKind

    if not config.defaults and not config.routes:
        return [
            Warning(
                "Taskport has no routes and no default backends, so every submit will fail.",
                hint="Set TASKPORT['defaults'] = {'task': '<backend>', 'job': '<backend>'} "
                "or add routes.",
                id=ID_NO_DEFAULT,
            )
        ]
    missing = [
        kind.value
        for kind in ExecutionKind
        if kind not in config.defaults
        and not any(route.kind in (None, kind) for route in config.routes)
    ]
    if missing:
        return [
            Warning(
                f"Taskport has no route or default for: {', '.join(missing)}. "
                f"Submitting one of those will raise RoutingError.",
                id=ID_NO_DEFAULT,
            )
        ]
    return []


def _check_tasks_setting(settings: Any) -> list[CheckMessage]:
    tasks = getattr(settings, "TASKS", None) or {}
    if not isinstance(tasks, dict):
        return []
    uses_bridge = any(
        "taskport_django" in str(entry.get("BACKEND", ""))
        for entry in tasks.values()
        if isinstance(entry, dict)
    )
    if not uses_bridge and getattr(settings, "TASKPORT", None):
        return [
            Warning(
                "settings.TASKPORT is configured but no TASKS backend routes through "
                "Taskport, so django.tasks enqueues will not reach it.",
                hint="Set TASKS = {'default': {'BACKEND': 'taskport_django.TaskportBackend'}} "
                "or use the runtime directly with taskport_django.get_runtime().",
                id=ID_NOT_WIRED,
            )
        ]
    return []


def _check_not_local_in_production(settings: Any, config: Any) -> list[CheckMessage]:
    """Warn when a non-DEBUG deployment is still on the in-process backends.

    They work, they are useful, and they lose every pending task on restart. That
    is fine in development and is very much not fine behind ``DEBUG = False``.
    """
    if getattr(settings, "DEBUG", False):
        return []
    in_process = {
        name
        for name, definition in config.backends.items()
        if definition.factory in {"thread", "process", "inline"}
    }
    routed = set(config.defaults.values()) | {route.backend for route in config.routes}
    at_risk = sorted(in_process & routed)
    if not at_risk:
        return []
    return [
        Warning(
            f"DEBUG is False but these Taskport backends run in this process and lose "
            f"pending work on restart: {', '.join(at_risk)}.",
            hint="Route production task queues at a durable engine "
            "(taskport-procrastinate, taskport-cloudtasks) and jobs at a batch runtime.",
            id=ID_LOCAL_IN_PRODUCTION,
        )
    ]


__all__ = [
    "ID_BACKEND",
    "ID_CONFIG",
    "ID_LOCAL_IN_PRODUCTION",
    "ID_NOT_WIRED",
    "ID_NO_DEFAULT",
    "check_taskport",
]
