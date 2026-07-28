"""Taskport on Cloud Run Jobs — serverless, run-to-completion workloads.

```mermaid
flowchart LR
    APP["Application"]
    TP["Taskport"]
    AD["taskport_cloudrun"]
    CR["Cloud Run Jobs"]
    C["container"]

    APP --> TP --> AD --> CR --> C
```

This is the adapter that proves Task and Job are different primitives. A Cloud
Run Job has its own container image, its own CPU/memory envelope, its own
timeout, and it scales to zero when nothing is running. None of that is
expressible as "a task that takes a while", and no task engine should be asked to
grow container semantics to pretend otherwise.

    from taskport import Taskport, Resources

    runtime = Taskport.from_mapping({
        "backends": {"heavy": {
            "factory": "cloudrun", "project": "my-project", "location": "europe-west1",
        }},
        "defaults": {"job": "heavy"},
    })

    handle = runtime.jobs.submit(
        "build-cog",
        args=["--input", "gs://bucket/scene.tif"],
        resources=Resources(cpu="4000m", memory="16Gi"),
    )

``google-cloud-run`` is imported lazily, inside the method that needs a client,
so importing this package — never mind ``taskport`` — costs nothing. Clients are
injectable, so the entire test suite runs with no GCP account and no credentials.

Cloud Run runs a **pre-declared** Job resource with per-execution overrides. The
image, CPU and memory live in the Job definition you deploy with Terraform or
``gcloud``; a submission overrides args, env, task count and timeout. That is why
this backend advertises ``ENVIRONMENT``, ``PARALLELISM`` and ``TIMEOUT`` but not
``CPU``, ``MEMORY`` or ``GPU`` — see :mod:`taskport_cloudrun.backend`.
"""

from __future__ import annotations

from .backend import (
    CLOUD_RUN_CAPABILITIES,
    CloudRunJobBackend,
    make_backend,
    map_execution_state,
)

__version__ = "0.2.0"

__all__ = [
    "CLOUD_RUN_CAPABILITIES",
    "CloudRunJobBackend",
    "__version__",
    "make_backend",
    "map_execution_state",
]
