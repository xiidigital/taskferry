"""Taskport backends for the Django 6 Tasks framework.

Each backend is a real ``django.tasks`` backend referenced from the ``TASKS``
setting. Provider SDKs are imported lazily inside each backend, so importing this
package pulls in no cloud SDK (section 31).
"""

from __future__ import annotations

from .cloud_tasks import CloudTasksBackend
from .local import LocalBackend
from .sqs import SQSBackend

__all__ = ["CloudTasksBackend", "LocalBackend", "SQSBackend"]
