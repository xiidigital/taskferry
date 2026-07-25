"""The JobRunner port and a base class that enforces the cross-cutting rules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from taskport.core import (
    ATTR_JOB_ID,
    ATTR_PROVIDER,
    SPAN_JOB_POLL,
    SPAN_JOB_SUBMIT,
    CapabilitySet,
    span,
)

from .capabilities import JobCapability
from .models import JobHandle, JobResult, JobSpec


@runtime_checkable
class JobRunner(Protocol):
    """Port for running finite workloads. Adapters implement this contract."""

    @property
    def provider(self) -> str: ...

    @property
    def capabilities(self) -> CapabilitySet: ...

    def run(self, spec: JobSpec) -> JobHandle: ...

    def get(self, handle: JobHandle) -> JobResult: ...

    def cancel(self, handle: JobHandle) -> None: ...


class BaseJobRunner(ABC):
    """Template base that applies validation, capability checks and tracing.

    Concrete runners implement ``_submit`` / ``_poll`` / ``_cancel`` and declare
    ``provider`` and ``capabilities``. The public ``run``/``get``/``cancel`` here
    guarantee the honest-capabilities rule (ADR-0005) uniformly.
    """

    @property
    @abstractmethod
    def provider(self) -> str: ...

    @property
    @abstractmethod
    def capabilities(self) -> CapabilitySet: ...

    @abstractmethod
    def _submit(self, spec: JobSpec) -> JobHandle: ...

    @abstractmethod
    def _poll(self, handle: JobHandle) -> JobResult: ...

    @abstractmethod
    def _cancel(self, handle: JobHandle) -> None: ...

    def run(self, spec: JobSpec) -> JobHandle:
        # Reject a spec that requests capabilities this runner lacks, loudly.
        self.capabilities.require_all(spec.required_capabilities())
        with span(SPAN_JOB_SUBMIT, {ATTR_PROVIDER: self.provider}) as s:
            handle = self._submit(spec)
            s.set_attribute(ATTR_JOB_ID, handle.id)
            return handle

    def get(self, handle: JobHandle) -> JobResult:
        with span(SPAN_JOB_POLL, {ATTR_PROVIDER: self.provider, ATTR_JOB_ID: handle.id}):
            return self._poll(handle)

    def cancel(self, handle: JobHandle) -> None:
        self.capabilities.require(JobCapability.CANCEL)
        self._cancel(handle)


__all__ = ["BaseJobRunner", "JobRunner"]
