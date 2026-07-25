"""Run a finite workload locally and poll it to completion.

uv run python examples/jobs-local/run.py
"""

from __future__ import annotations

import sys

from taskport.jobs import JobSpec, runners


def main() -> None:
    runner = runners["default"]  # LocalJobRunner out of the box
    print("provider:", runner.provider, "capabilities:", sorted(runner.capabilities))

    handle = runner.run(
        JobSpec(
            name="demo-etl",
            command=[sys.executable, "-c", "print('processing rows...'); print('done')"],
        )
    )
    print("submitted:", handle.id, "->", handle.provider_metadata.provider_id)

    result = runner.wait(handle, timeout=10)  # type: ignore[attr-defined]
    print("status:", result.status, "exit_code:", result.exit_code)
    if result.logs_uri:
        with open(result.logs_uri) as log:
            print("logs:\n" + log.read().rstrip())


if __name__ == "__main__":
    main()
