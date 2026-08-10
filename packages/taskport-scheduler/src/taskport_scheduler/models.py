"""Portable scheduler domain types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from taskport.core import ProviderMetadata, ProviderOptions, TaskportId

from .targets import Target
from .triggers import Trigger


class ScheduleStatus(StrEnum):
    """Whether a schedule is currently active."""

    ENABLED = "enabled"
    PAUSED = "paused"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Schedule:
    """A portable description of when to fire what.

    Attributes:
        name: Unique, human-readable schedule name.
        trigger: When to fire (cron/interval/one-shot).
        target: What to fire (HTTP/PubSub/callable).
        enabled: Whether it starts active.
        description: Optional human description.
        labels: Provider labels.
        provider_options: Provider-specific escape hatch (ADR-0007).
    """

    name: str
    trigger: Trigger
    target: Target
    enabled: bool = True
    description: str = ""
    labels: Mapping[str, str] = field(default_factory=dict)
    provider_options: ProviderOptions = field(default_factory=ProviderOptions)

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))


@dataclass(frozen=True, slots=True)
class ScheduleHandle:
    """A reference to a created schedule."""

    id: TaskportId
    name: str
    status: ScheduleStatus
    provider_metadata: ProviderMetadata

    @property
    def provider(self) -> str:
        return self.provider_metadata.provider


@dataclass(frozen=True, slots=True)
class FireRecord:
    """A record that a schedule fired (used by the local scheduler)."""

    schedule_name: str
    fired_at: datetime
    result: object = None


__all__ = ["FireRecord", "Schedule", "ScheduleHandle", "ScheduleStatus"]
