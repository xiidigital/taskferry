"""The sixty-second tour: three primitives, one runtime, zero infrastructure.

uv run python examples/quickstart/run.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import work

from taskport import Capability, ExecutionState, Taskport


def main() -> None:
    runtime = Taskport.local()

    # -- Inline: run it here, now. Accepts any callable, even a lambda. ------- #
    inline = runtime.inline.submit(lambda a, b: a + b, 20, 22)
    print(f"inline   {inline.state.value:>10}  value={inline.result().value}")

    # -- Task: a named function, handed to an engine. ------------------------- #
    # Locally that engine is a thread pool. In production it is Procrastinate or
    # Cloud Tasks, and this line does not change.
    #
    # Note the function comes from an importable module: a worker has to be able
    # to find it. Passing a lambda here raises, with an explanation.
    task = runtime.tasks.submit(work.add, 1, 2, queue="default")
    task.wait(10)
    print(f"task     {task.state.value:>10}  value={task.result().value}")

    # -- Job: an isolated workload, run to completion. ------------------------ #
    # Locally that is a subprocess. In production it is Cloud Run Jobs or
    # Kubernetes, and this line does not change either.
    job = runtime.jobs.submit("greet", command=["python", "-c", "print('hello from a job')"])
    job.wait(30)
    print(f"job      {job.state.value:>10}  exit={job.result().exit_code}")

    # -- Capabilities are honest --------------------------------------------- #
    print("\nwhat each backend can actually do:")
    for name in runtime.backend_names():
        caps = ", ".join(sorted(str(c) for c in runtime.capabilities(name)))
        print(f"  {name:<12} {caps}")

    # A thread pool is FIFO, so it does not advertise PRIORITY. Asking for one
    # raises rather than being quietly ignored.
    print("\nasking a FIFO backend for a priority:")
    try:
        runtime.tasks.submit(work.add, 1, 1, priority=9)
    except Exception as exc:
        print(f"  {type(exc).__name__}: {exc}")

    # And a lambda as a *task* is refused, for the reason above.
    print("\nasking for a lambda to run on a worker:")
    try:
        runtime.tasks.submit(lambda: None)
    except Exception as exc:
        print(f"  {type(exc).__name__}: {exc}")

    assert Capability.PRIORITY not in runtime.capabilities("thread")
    assert inline.state is ExecutionState.SUCCEEDED

    runtime.close()


if __name__ == "__main__":
    main()
