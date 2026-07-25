"""Job runner capabilities (ADR-0005, section 13)."""

from __future__ import annotations

from taskport.core import Capability


class JobCapability(Capability):
    """What a job runner can do. Providers advertise a subset."""

    STATUS = "status"
    CANCEL = "cancel"
    LOGS = "logs"
    TIMEOUT = "timeout"
    PARALLELISM = "parallelism"
    CPU_OVERRIDE = "cpu_override"
    MEMORY_OVERRIDE = "memory_override"
    GPU = "gpu"
    ENVIRONMENT_OVERRIDE = "environment_override"


__all__ = ["JobCapability"]
