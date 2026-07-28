"""Backends that ship with Taskport and need nothing installed.

```mermaid
flowchart TD
    RT["Taskport runtime"]

    RT -->|"inline"| I["InlineExecutionBackend<br/>this thread, right now"]
    RT -->|"task"| T["ThreadTaskBackend<br/>ThreadPoolExecutor"]
    RT -->|"task"| P["ProcessTaskBackend<br/>ProcessPoolExecutor"]
    RT -->|"job"| S["SubprocessJobBackend<br/>child processes"]
```

These exist so that Taskport is useful with zero infrastructure — in a test, a
notebook, a CLI, a script, a small application — and so that every port has at
least one honest reference implementation to test adapters against.

They are **not** a queue. There is no durability, no cross-process visibility and
no delivery guarantee beyond "this process tried". A thread pool that dies takes
its pending work with it. That is stated plainly rather than papered over, and it
is exactly why the Procrastinate and Cloud Run adapters exist.

Each module imports lazily from this package, so ``import taskport`` does not
create a process pool or touch the filesystem.
"""

from __future__ import annotations

from .inline import InlineExecutionBackend
from .process import ProcessTaskBackend
from .subprocess import SubprocessJobBackend
from .thread import ThreadTaskBackend

__all__ = [
    "InlineExecutionBackend",
    "ProcessTaskBackend",
    "SubprocessJobBackend",
    "ThreadTaskBackend",
]
