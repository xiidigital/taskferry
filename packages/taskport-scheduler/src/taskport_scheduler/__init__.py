"""``taskport_scheduler`` — portable "when to fire" triggering.

A *Schedule* decides **when**; it does not run business logic itself. It triggers
another capability — a Task, a Job, or an Event — by composition (sections 12,
45). Plain Python, no Django required (section 57):

    from taskport_scheduler import Schedule, CronTrigger, HttpTarget, schedulers

    handle = schedulers["default"].create(
        Schedule(
            name="nightly",
            trigger=CronTrigger("0 2 * * *"),
            target=HttpTarget("https://svc/_taskport/execute"),
        )
    )

The default scheduler is in-process (`LocalScheduler`, interval/one-shot).
Provider adapters (GCP Cloud Scheduler, and more on the roadmap) live under
``taskport_scheduler.adapters`` and import their SDK lazily (section 31).
"""

from __future__ import annotations

from .adapters.local import LocalScheduler
from .capabilities import ScheduleCapability
from .errors import ScheduleError, ScheduleNotFoundError
from .models import FireRecord, Schedule, ScheduleHandle, ScheduleStatus
from .registry import schedulers
from .scheduler import BaseScheduler, Scheduler
from .targets import CallableTarget, HttpTarget, PubSubTarget, Target
from .triggers import CronTrigger, IntervalTrigger, OneShotTrigger, Trigger

__version__ = "0.1.0"

__all__ = [
    "BaseScheduler",
    "CallableTarget",
    "CronTrigger",
    "FireRecord",
    "HttpTarget",
    "IntervalTrigger",
    "LocalScheduler",
    "OneShotTrigger",
    "PubSubTarget",
    "Schedule",
    "ScheduleCapability",
    "ScheduleError",
    "ScheduleHandle",
    "ScheduleNotFoundError",
    "ScheduleStatus",
    "Scheduler",
    "Target",
    "Trigger",
    "__version__",
    "schedulers",
]
