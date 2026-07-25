"""``taskport.django`` — portable backends for the official Django 6 Tasks API.

You keep writing standard Django:

    from django.tasks import task

    @task
    def resize_image(image_id: int) -> None:
        ...

Taskport does not add a parallel task API. It provides **backends** you select
via the official ``TASKS`` setting (never a ``TASKPORT_TASKS`` duplicate,
section 36)::

    TASKS = {
        "default": {
            "BACKEND": "taskport.django.backends.cloud_tasks.CloudTasksBackend",
            "OPTIONS": {...},
        }
    }

Backends: ``LocalBackend`` (dev), ``CloudTasksBackend`` (GCP serverless push),
``SQSBackend`` (AWS). See ``taskport.django.views.task_webhook`` and
``taskport.django.consumers`` for the receive side.
"""

from __future__ import annotations

from .base import TaskportCapabilityMixin, TaskportTaskBackend
from .capabilities import TaskCapability
from .consumers import process_sqs_event, process_sqs_message
from .execution import execute_task_message
from .message import TaskMessage, build_message, resolve_task

__version__ = "0.1.0"

__all__ = [
    "TaskCapability",
    "TaskMessage",
    "TaskportCapabilityMixin",
    "TaskportTaskBackend",
    "__version__",
    "build_message",
    "execute_task_message",
    "process_sqs_event",
    "process_sqs_message",
    "resolve_task",
]
