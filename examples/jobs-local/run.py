"""Jobs, end to end, with no infrastructure at all.

    uv run python examples/jobs-local/run.py

A Job is not a long Task. It has its own argv, its own environment, its own
resource envelope and its own lifecycle, and it returns an **exit code** rather
than a Python value. This example exercises all of that against the built-in
subprocess backend — the same `JobSpec` that Cloud Run, Kubernetes or AWS Batch
would receive in production.
"""

from __future__ import annotations

import sys

from taskport import Capability, ExecutionState, Resources, Taskport
from taskport.errors import UnsupportedCapability


def main() -> None:
    runtime = Taskport.local()
    backend = runtime.backend("subprocess")

    print("what the local job backend can do:")
    print(f"  {', '.join(sorted(str(c) for c in backend.capabilities))}\n")

    # -- a job that succeeds -------------------------------------------------- #
    ok = runtime.jobs.submit(
        "greet",
        command=[sys.executable, "-c", "print('hello from a job')"],
    )
    ok.wait(30)
    print(f"succeeds   {ok.state.value:>10}  exit={ok.result().exit_code}")
    print(f"           logs: {ok.execution.result.logs_uri}")

    # -- a job that fails, and says how --------------------------------------- #
    failed = runtime.jobs.submit(
        "explode",
        command=[sys.executable, "-c", "import sys; print('bad input'); sys.exit(3)"],
    )
    final = failed.wait(30)
    print(f"\nfails      {final.state.value:>10}  exit={final.result.exit_code}")
    print(f"           error: {final.result.error}")

    # -- environment reaches the child ---------------------------------------- #
    env = runtime.jobs.submit(
        "env",
        command=[sys.executable, "-c", "import os; print('DATASET =', os.environ['DATASET'])"],
        env={"DATASET": "scene-42"},
    )
    env.wait(30)
    print(f"\nenv        {env.state.value:>10}")
    print(f"           {backend.logs(env.id).strip()}")

    # -- a timeout really stops the work -------------------------------------- #
    slow = runtime.jobs.submit(
        "sleepy",
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=0.3,
    )
    print(f"\ntimeout    {slow.wait(30).state.value:>10}  (the child was terminated)")

    # -- cancellation, where the backend really supports it -------------------- #
    assert Capability.CANCEL in backend.capabilities
    long = runtime.jobs.submit(
        "long", command=[sys.executable, "-c", "import time; time.sleep(30)"]
    )
    print(f"cancel     {long.cancel().state.value:>10}")

    # -- and the refusal that matters most ------------------------------------ #
    print("\nasking a subprocess backend for two GPUs:")
    try:
        runtime.jobs.submit("train", command=["true"], resources=Resources(gpu=2))
    except UnsupportedCapability as exc:
        print(f"  {type(exc).__name__}: {exc}")
        print("  ...rather than running on the CPU and returning numbers nobody can explain.")

    print("\nThe same JobSpec, routed at 'cloudrun' or 'kubernetes' in production,")
    print("would run in a container. Nothing above would change but the config.")

    assert ok.state is ExecutionState.SUCCEEDED
    assert final.state is ExecutionState.FAILED
    assert slow.execution.state is ExecutionState.TIMED_OUT
    runtime.close()


if __name__ == "__main__":
    main()
