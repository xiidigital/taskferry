"""Portable job domain types.

A *Job* is a finite workload run to completion in its own process/container
(ETL, GIS, raster, tiles, ML, batch imports). Exactly one execution per
submission; you poll status, cancel, and read logs (ADR-0002, section 9).

All types are immutable (frozen dataclasses) per the immutability rule (section
6). Payloads that cross to a provider stay JSON-shaped: pass identifiers and
argv, never live objects (ADR-0008).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from taskport.core import (
    Correlation,
    ProviderMetadata,
    ProviderOptions,
    TaskportId,
)

from .capabilities import JobCapability


class JobStatus(StrEnum):
    """Lifecycle state of a job execution."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

    @property
    def is_terminal(self) -> bool:
        return self in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


@dataclass(frozen=True, slots=True)
class JobResources:
    """Requested compute resources. Fields are provider-agnostic strings/ints.

    ``cpu`` and ``memory`` use Kubernetes-style quantities (``"1000m"``,
    ``"512Mi"``) which every adapter can translate; ``gpu`` is a count.
    """

    cpu: str | None = None
    memory: str | None = None
    gpu: int = 0
    gpu_type: str | None = None


@dataclass(frozen=True, slots=True)
class JobSpec:
    """A portable description of a workload to run.

    Attributes:
        name: Human-readable job name (also used to locate the provider resource
            for container runners unless overridden via ``provider_options``).
        command: Full argv to execute. Required for the local runner; used as the
            container entrypoint/args override for container runners.
        image: Container image. Ignored by the local runner; required by
            container/cloud runners.
        env: Environment variables to inject.
        resources: Requested CPU/memory/GPU.
        timeout: Wall-clock timeout in seconds, or ``None`` for no limit.
        parallelism: Number of parallel task instances (array/indexed jobs).
        max_retries: Provider-native retry count on failure.
        labels: Arbitrary key/value metadata for the provider.
        provider_options: Provider-specific escape hatch (ADR-0006, section 17).
        correlation: Correlation to propagate for observability (section 19).
    """

    name: str
    command: Sequence[str] = ()
    image: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    resources: JobResources = field(default_factory=JobResources)
    timeout: float | None = None
    parallelism: int = 1
    max_retries: int = 0
    labels: Mapping[str, str] = field(default_factory=dict)
    provider_options: ProviderOptions = field(default_factory=ProviderOptions)
    correlation: Correlation | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "command", tuple(self.command))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))
        if self.parallelism < 1:
            raise ValueError("parallelism must be >= 1")
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError("timeout must be positive when set")

    def required_capabilities(self) -> frozenset[JobCapability]:
        """Capabilities this spec needs a runner to support.

        Used to enforce the honest-capabilities rule (ADR-0005): a runner rejects
        a spec that asks for something it cannot do, instead of silently ignoring
        the request.
        """
        required: set[JobCapability] = set()
        if self.timeout is not None:
            required.add(JobCapability.TIMEOUT)
        if self.parallelism > 1:
            required.add(JobCapability.PARALLELISM)
        if self.resources.cpu is not None:
            required.add(JobCapability.CPU_OVERRIDE)
        if self.resources.memory is not None:
            required.add(JobCapability.MEMORY_OVERRIDE)
        if self.resources.gpu > 0:
            required.add(JobCapability.GPU)
        if self.env:
            required.add(JobCapability.ENVIRONMENT_OVERRIDE)
        return frozenset(required)


@dataclass(frozen=True, slots=True)
class JobHandle:
    """An opaque, portable reference to a submitted job.

    Carries both the Taskport id (always Taskport-owned) and provider metadata
    with the provider's own id, so a job is followable across systems
    (sections 20, 48).
    """

    id: TaskportId
    name: str
    provider_metadata: ProviderMetadata
    created_at: datetime
    correlation: Correlation | None = None

    @property
    def provider(self) -> str:
        return self.provider_metadata.provider


@dataclass(frozen=True, slots=True)
class JobResult:
    """The observed outcome of a job execution at a point in time."""

    handle: JobHandle
    status: JobStatus
    exit_code: int | None = None
    error: str | None = None
    logs_uri: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal


__all__ = [
    "JobHandle",
    "JobResources",
    "JobResult",
    "JobSpec",
    "JobStatus",
]
