"""Minimal Django settings for the taskport-django test suite."""

from __future__ import annotations

SECRET_KEY = "taskport-test-secret-not-for-production"
USE_TZ = True

INSTALLED_APPS: list[str] = []

DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
}

# Provider backends are configured with injected fake clients so the suite needs
# no cloud account. Aliases must exist so deferred tasks (validated against their
# backend at construction) can target a defer-capable backend.
import fakes  # noqa: E402 - test-only module on pythonpath

TASKS = {
    "default": {"BACKEND": "taskport.django.backends.local.LocalBackend"},
    "cloudtasks": {
        "BACKEND": "taskport.django.backends.cloud_tasks.CloudTasksBackend",
        "OPTIONS": {
            "project": "p",
            "location": "us-central1",
            "queue": "default",
            "url": "https://svc.run.app/_taskport/execute",
            "client": fakes.CLOUD_TASKS_CLIENT,
        },
    },
    "sqs": {
        "BACKEND": "taskport.django.backends.sqs.SQSBackend",
        "OPTIONS": {
            "queue_url": "https://sqs.us-east-1.amazonaws.com/1/q",
            "client": fakes.SQS_CLIENT,
        },
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
