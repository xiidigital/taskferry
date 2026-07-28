"""Django settings in, :class:`~taskport.TaskportConfig` out.

```mermaid
flowchart LR
    S["settings.TASKPORT<br/>(a plain dict)"]
    F["config_from_settings()"]
    C["TaskportConfig"]
    R["Taskport runtime"]

    S --> F --> C --> R
```

This module is the *only* place in the whole project where Django settings are
read, and it lives in the Django adapter — which is exactly where the layering
says it belongs. ``taskport.config`` cannot import ``django.conf``, so a
misplaced settings read is a test failure, not a review comment.

The setting is an ordinary dict in the shape
:meth:`~taskport.TaskportConfig.from_mapping` already understands, so nothing new
has to be learned to configure Taskport from Django, and the same dict can be
lifted verbatim into a FastAPI service or a CLI when that day comes.

    TASKPORT = {
        "backends": {
            "pg":    {"factory": "procrastinate", "app": "myapp.tasks:app"},
            "heavy": {"factory": "cloudrun", "project": "p", "location": "europe-west1"},
        },
        "routes": [
            {"kind": "task", "queue": "metadata", "backend": "pg"},
            {"kind": "job",  "profile": "heavy",  "backend": "heavy"},
        ],
        "defaults": {"task": "pg", "job": "heavy"},
    }

With no ``TASKPORT`` setting at all, :func:`config_from_settings` falls back to
:meth:`TaskportConfig.local` — so a fresh project runs tasks on a thread pool and
jobs as subprocesses, works immediately, and is obviously not production. That is
better than either crashing on first use or quietly doing nothing.
"""

from __future__ import annotations

import threading
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from taskport import Taskport, TaskportConfig
from taskport.errors import ConfigurationError

SETTING_NAME = "TASKPORT"

_runtime: Taskport | None = None
_lock = threading.Lock()


def config_from_settings(settings: Any = None) -> TaskportConfig:
    """Build a :class:`~taskport.TaskportConfig` from ``settings.TASKPORT``.

    Args:
        settings: Settings object to read. Defaults to ``django.conf.settings``;
            injectable so the loader is testable without a settings module.

    Raises:
        ImproperlyConfigured: when ``TASKPORT`` is present but malformed. Raising
            Django's own exception type means the error surfaces through
            ``manage.py check`` and the usual startup path, in the form a Django
            developer already knows how to read.
    """
    source = settings if settings is not None else _django_settings()
    raw = getattr(source, SETTING_NAME, None)
    if raw is None:
        return TaskportConfig.local()
    if not isinstance(raw, dict):
        raise ImproperlyConfigured(
            f"settings.{SETTING_NAME} must be a dict, got {type(raw).__name__}"
        )
    try:
        return TaskportConfig.from_mapping(raw)
    except ConfigurationError as exc:
        raise ImproperlyConfigured(f"settings.{SETTING_NAME} is invalid: {exc}") from exc


def get_runtime(settings: Any = None) -> Taskport:
    """The process-wide runtime, built once from settings.

    A single shared runtime is deliberate: backends hold connection pools and
    clients, and building one per request would open a new pool per request. The
    runtime is thread-safe, which is what makes sharing it correct under any
    Django deployment model.

    Use :func:`reset_runtime` in tests, or ``override_settings`` plus a reset.
    """
    global _runtime
    with _lock:
        if _runtime is None:
            _runtime = Taskport(config=config_from_settings(settings))
        return _runtime


def reset_runtime() -> None:
    """Close and forget the shared runtime. For tests and settings changes."""
    global _runtime
    with _lock:
        existing, _runtime = _runtime, None
    if existing is not None:
        existing.close()


def _django_settings() -> Any:
    from django.conf import settings

    return settings


__all__ = ["SETTING_NAME", "config_from_settings", "get_runtime", "reset_runtime"]
