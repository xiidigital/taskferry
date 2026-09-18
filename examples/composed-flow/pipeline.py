"""A task that launches a job — and why that boundary is where it is.

The task decides *what should happen next*. Taskferry decides *how and where* it
runs. That split is the whole reason Taskferry is not a workflow engine: the
business meaning of "after metadata extraction, build a COG" belongs to your
application, not to the execution layer.
"""

from __future__ import annotations

import sys
from typing import Any

from taskferry import Taskferry

# Set by run.py so the task can reach the runtime. In a real deployment this
# would be your application's own runtime accessor — Django's get_runtime(), a
# FastAPI dependency, a module-level singleton you built at startup.
RUNTIME: Taskferry | None = None

BUILT: list[str] = []


def extract_metadata(dataset_id: int) -> dict[str, Any]:
    """A short task: cheap, in-process on a worker, returns a value."""
    metadata = {"dataset_id": dataset_id, "bands": 4, "needs_cog": True}

    if metadata["needs_cog"] and RUNTIME is not None:
        # Heavy work does not belong on a task worker: it needs its own image,
        # its own memory, and its own lifecycle. Hand it to the job port and let
        # routing decide whether that means a subprocess or Cloud Run.
        RUNTIME.jobs.submit(
            f"build-cog-{dataset_id}",
            command=[sys.executable, "-c", f"print('building COG for {dataset_id}')"],
            profile="heavy",
            labels={"dataset_id": str(dataset_id)},
        )
        BUILT.append(f"build-cog-{dataset_id}")

    return metadata
