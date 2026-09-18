"""Batch job backends for Taskferry: AWS Batch, Kubernetes, Azure Container Apps.

```mermaid
flowchart TD
    JS["JobSpec<br/>image · argv · resources · timeout"]
    TP["Taskferry router"]

    JS --> TP

    TP -->|"profile=gpu"| K8S["KubernetesJobBackend"]
    TP -->|"profile=batch"| AWS["BatchJobBackend"]
    TP -->|"profile=azure"| ACA["ContainerAppsJobBackend"]
    TP -->|"profile=local"| SUB["taskferry's subprocess backend"]
```

Three runtimes, one `JobSpec`, and honest differences between them:

| Capability     | AWS Batch | Kubernetes | Container Apps | Cloud Run | subprocess |
| -------------- | :-------: | :--------: | :------------: | :-------: | :--------: |
| `CPU`/`MEMORY` |     yes   |    yes     |      yes       |    no     |     no     |
| `GPU`          |     yes   |    yes     |      no        |    no     |     no     |
| `PARALLELISM`  |     yes   |    yes     |      yes       |    yes    |     no     |
| `TIMEOUT`      |     yes   |    yes     |      yes       |    yes    |     yes    |
| `RESULT`       |     yes   |    no      |      no        |    no     |     yes    |

Those "no"s are the point. Submitting a GPU spec to Container Apps raises
:class:`~taskferry.errors.UnsupportedCapability` at submit time rather than
running the workload on a CPU and returning numbers that look plausible.

Every provider SDK is imported lazily inside the backend that needs it, so
``import taskferry_jobs`` pulls in neither ``boto3`` nor ``kubernetes`` nor the
Azure SDK. Install only the extra you use::

    pip install 'taskferry-jobs[aws]'
    pip install 'taskferry-jobs[kubernetes]'
    pip install 'taskferry-jobs[azure]'

Cloud Run Jobs lives in its own distribution (``taskferry-cloudrun``); local
subprocess execution is built into ``taskferry`` itself.
"""

from __future__ import annotations

from .aws import BATCH_CAPABILITIES, BatchJobBackend, map_batch_state
from .azure import (
    CONTAINER_APPS_CAPABILITIES,
    ContainerAppsJobBackend,
    map_execution_state,
)
from .kubernetes import KUBERNETES_CAPABILITIES, KubernetesJobBackend, map_job_state

__version__ = "0.2.0"

__all__ = [
    "BATCH_CAPABILITIES",
    "CONTAINER_APPS_CAPABILITIES",
    "KUBERNETES_CAPABILITIES",
    "BatchJobBackend",
    "ContainerAppsJobBackend",
    "KubernetesJobBackend",
    "__version__",
    "map_batch_state",
    "map_execution_state",
    "map_job_state",
]
