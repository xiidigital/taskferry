"""Reusable contract suites every adapter must pass.

An adapter's job is to be *interchangeable*. That is only true if every adapter
behaves the same way at the port, so the port ships its own test suite and each
adapter runs it:

```mermaid
flowchart TD
    SUITE["taskport.contract<br/>TaskBackendContract · JobBackendContract"]

    PRO["taskport-procrastinate tests"]
    CT["taskport-cloudtasks tests"]
    CR["taskport-cloudrun tests"]
    LOCAL["built-in backend tests"]

    SUITE --> PRO
    SUITE --> CT
    SUITE --> CR
    SUITE --> LOCAL
```

The suites are **capability-driven**. A backend that does not advertise ``CANCEL``
is not skipped — it is asserted to *reject* cancellation with
:class:`~taskport.errors.UnsupportedCapability`. That is the assertion that
matters, because the failure mode this design exists to prevent is a backend
quietly pretending.

Usage in an adapter's test module::

    from taskport.contract import TaskBackendContract

    class TestMyBackend(TaskBackendContract):
        def make_backend(self):
            return MyTaskBackend(client=FakeClient())

        def success_spec(self):
            return TaskSpec(task="tests.tasks:ok")

This module imports :mod:`pytest`, so it belongs in test environments. It is
shipped inside the ``taskport`` distribution anyway: an adapter released
separately must be able to import the suite it is required to pass.
"""

from __future__ import annotations

from .base import ExecutionBackendContract
from .inline import InlineBackendContract
from .job import JobBackendContract
from .task import TaskBackendContract

__all__ = [
    "ExecutionBackendContract",
    "InlineBackendContract",
    "JobBackendContract",
    "TaskBackendContract",
]
