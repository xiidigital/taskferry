"""Schedule triggers — the "when".

Three portable trigger shapes. Each maps to the scheduler capability it requires
so adapters can reject what they cannot honor (ADR-0005).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .capabilities import ScheduleCapability


@dataclass(frozen=True, slots=True)
class CronTrigger:
    """Fire on a unix-cron schedule. ``timezone`` is an IANA name (e.g. UTC)."""

    expression: str
    timezone: str = "UTC"

    def required_capability(self) -> ScheduleCapability:
        return ScheduleCapability.CRON


@dataclass(frozen=True, slots=True)
class IntervalTrigger:
    """Fire every ``seconds`` seconds."""

    seconds: float

    def __post_init__(self) -> None:
        if self.seconds <= 0:
            raise ValueError("interval seconds must be positive")

    def required_capability(self) -> ScheduleCapability:
        return ScheduleCapability.INTERVAL


@dataclass(frozen=True, slots=True)
class OneShotTrigger:
    """Fire once at ``at`` (an aware datetime)."""

    at: datetime

    def required_capability(self) -> ScheduleCapability:
        return ScheduleCapability.ONE_SHOT


Trigger = CronTrigger | IntervalTrigger | OneShotTrigger


__all__ = ["CronTrigger", "IntervalTrigger", "OneShotTrigger", "Trigger"]
