"""Task -> Job composition, with correlation carried across the boundary.

    uv run python examples/composed-flow/run.py

Shows the frontier from the design: the application knows *what* the pipeline
means; Taskport only knows how and where each step executes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pipeline

from taskport import Correlation, Taskport, use_correlation
from taskport.core.correlation import current_correlation

CONFIG = {
    "backends": {
        "fast": {"factory": "thread", "max_workers": 2},
        "heavy": {"factory": "subprocess"},
    },
    "routes": [
        {"kind": "task", "queue": "metadata", "backend": "fast"},
        {"kind": "job", "profile": "heavy", "backend": "heavy"},
    ],
    "defaults": {"task": "fast", "job": "heavy"},
}


def main() -> None:
    runtime = Taskport.from_mapping(CONFIG)
    pipeline.RUNTIME = runtime

    # One correlation id ties the whole flow together — the HTTP request that
    # started it, the task, and the job the task launched.
    flow = Correlation.start()
    print(f"flow correlation: {flow.correlation_id}\n")

    with use_correlation(flow):
        assert current_correlation() is flow
        task = runtime.tasks.submit(pipeline.extract_metadata, 42, queue="metadata")

    final = task.wait(20)
    print(f"task  {final.state.value:>10}  {task.result().value}")
    print(f"      correlation: {final.correlation.correlation_id if final.correlation else '-'}")
    print(f"\nthe task launched: {pipeline.BUILT}")

    print("\nwhat Taskport did NOT do:")
    print("  - decide that a COG follows metadata extraction (your domain)")
    print("  - persist a pipeline state machine (your database)")
    print("  - retry the pipeline as a unit (a workflow engine's job)")
    print("\nwhat it did do: route two different kinds of work to two engines,")
    print("and carry one correlation id across the boundary between them.")

    runtime.close()


if __name__ == "__main__":
    main()
