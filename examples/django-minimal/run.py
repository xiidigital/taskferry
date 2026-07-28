"""The standard django.tasks API, running on a Taskport backend.

    uv run python examples/django-minimal/run.py

The point of this example is how little there is to it. Application code is
ordinary Django; the engine is a settings change.
"""

from __future__ import annotations

import sys
from pathlib import Path

import django
from django.conf import settings

sys.path.insert(0, str(Path(__file__).parent))

settings.configure(
    DEBUG=True,
    USE_TZ=True,
    INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "taskport_django"],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    # The standard Django setting, pointed at the bridge.
    TASKS={"default": {"BACKEND": "taskport_django.TaskportBackend"}},
    # And the Taskport configuration it bridges to. Swap "thread" for
    # {"factory": "procrastinate", "app": "myapp.tasks:app"} and nothing else
    # in this file — or in your application — changes.
    TASKPORT={
        "backends": {
            "now": {"factory": "inline"},
            "fast": {"factory": "thread", "max_workers": 2},
            "batch": {"factory": "subprocess"},
        },
        "routes": [{"kind": "task", "queue": "metadata", "backend": "fast"}],
        "defaults": {"inline": "now", "task": "fast", "job": "batch"},
    },
)
django.setup()

import app_tasks  # noqa: E402

from taskport_django import get_runtime  # noqa: E402


def main() -> None:
    runtime = get_runtime()

    # Standard Django. No Taskport import at the call site.
    result = app_tasks.resize_image.enqueue(42, width=1200)
    print(f"enqueued via django.tasks: id={result.id} status={result.status}")

    # Taskport's own handle for the same execution, with the fuller vocabulary.
    handle = runtime.get(result.id)
    handle.wait(10)
    print(f"taskport handle:           state={handle.state.value} value={handle.result().value}")

    metadata = app_tasks.extract_metadata.enqueue(7)
    runtime.get(metadata.id).wait(10)
    print(f"routed by queue_name:      {runtime.get(metadata.id).backend}")

    # The bridge answers "what can this backend do?" from the routed engine,
    # not from a hardcoded flag.
    from taskport_django import TaskportBackend

    backend = TaskportBackend("default", {})
    caps = ", ".join(sorted(str(c) for c in backend.taskport_capabilities("metadata")))
    print(f"\ncapabilities behind queue 'metadata': {caps}")

    # Every configured backend is actually built here, so a missing adapter or a
    # missing option is caught at `manage.py check` time rather than at 3am.
    print("\nsystem checks:")
    from taskport_django.checks import check_taskport

    messages = check_taskport()
    for message in messages:
        print(f"  [{message.id}] {message.msg}")
    if not messages:
        print("  (none — the configuration is coherent)")


if __name__ == "__main__":
    main()
