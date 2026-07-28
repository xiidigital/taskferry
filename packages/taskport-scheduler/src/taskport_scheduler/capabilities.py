"""Scheduler capabilities (ADR-0005, section 13).

Schedulers differ sharply: Cloud Scheduler is cron-only; EventBridge Scheduler
adds one-time schedules; a local scheduler does intervals easily but not cron.
The capability model makes those differences explicit instead of pretending they
are the same.
"""

from __future__ import annotations

from taskport.core import Capability


class ScheduleCapability(Capability):
    """What a scheduler can do. Providers advertise a subset."""

    ONE_SHOT = "one_shot"
    INTERVAL = "interval"
    CRON = "cron"
    TIMEZONE = "timezone"
    PAUSE = "pause"
    RESUME = "resume"
    UPDATE = "update"
    DELETE = "delete"


__all__ = ["ScheduleCapability"]
