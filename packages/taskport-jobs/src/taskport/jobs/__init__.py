"""``taskport.jobs`` — portable execution of finite workloads.

A *Job* runs a finite workload to completion in its own process/container (ETL,
GIS, raster, tiles, ML, batch imports). This package is plain Python and does
**not** require Django (section 57):

    from taskport.jobs import JobSpec, runners

    handle = runners["default"].run(JobSpec(name="etl", command=["python", "etl.py"]))
    result = runners["default"].get(handle)

Runner adapters (local, GCP Cloud Run Jobs, AWS Batch, Azure Container Apps Jobs,
Kubernetes) live under ``taskport.jobs.runners`` and import their provider SDK
lazily, so importing this package never pulls in a cloud SDK (section 31).
"""

from __future__ import annotations

from .capabilities import JobCapability
from .errors import JobError, JobNotFoundError
from .models import (
    JobHandle,
    JobResources,
    JobResult,
    JobSpec,
    JobStatus,
)
from .registry import runners
from .runner import BaseJobRunner, JobRunner

__version__ = "0.1.0"

__all__ = [
    "BaseJobRunner",
    "JobCapability",
    "JobError",
    "JobHandle",
    "JobNotFoundError",
    "JobResources",
    "JobResult",
    "JobRunner",
    "JobSpec",
    "JobStatus",
    "__version__",
    "runners",
]
