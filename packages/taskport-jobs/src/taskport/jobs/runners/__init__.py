"""Job runner adapters.

Importing this package is cheap: each adapter imports its provider SDK lazily,
inside the method that needs it, so ``import taskport.jobs`` (which imports this)
never pulls in ``boto3``, ``google.cloud``, ``azure`` or ``kubernetes``
(section 31, verified by tests).
"""

from __future__ import annotations

from .aws import BatchRunner, make_batch_runner
from .azure import ContainerAppsJobsRunner, make_container_apps_jobs_runner
from .gcp import CloudRunJobsRunner, make_cloud_run_jobs_runner
from .kubernetes import KubernetesJobRunner, make_kubernetes_job_runner
from .local import LocalJobRunner, make_local_job_runner

__all__ = [
    "BatchRunner",
    "CloudRunJobsRunner",
    "ContainerAppsJobsRunner",
    "KubernetesJobRunner",
    "LocalJobRunner",
    "make_batch_runner",
    "make_cloud_run_jobs_runner",
    "make_container_apps_jobs_runner",
    "make_kubernetes_job_runner",
    "make_local_job_runner",
]
