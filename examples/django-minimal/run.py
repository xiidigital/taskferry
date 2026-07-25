"""The official django.tasks API running on a Taskport backend.

    uv run python examples/django-minimal/run.py

Swap only the TASKS ``BACKEND`` below to move to Cloud Tasks / SQS — the task
code (``app_tasks.py``) never changes.
"""

from __future__ import annotations

import os
import sys

import django
from django.conf import settings

sys.path.insert(0, os.path.dirname(__file__))

settings.configure(
    SECRET_KEY="example-only",
    USE_TZ=True,
    INSTALLED_APPS=[],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    TASKS={"default": {"BACKEND": "taskport.django.backends.local.LocalBackend"}},
)
django.setup()

import app_tasks  # noqa: E402 - must follow django.setup()


def main() -> None:
    result = app_tasks.resize_image.enqueue(42)
    print("status:", result.status, "| return_value:", result.return_value)


if __name__ == "__main__":
    main()
