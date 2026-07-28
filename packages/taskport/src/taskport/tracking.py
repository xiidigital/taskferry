"""Pairing Taskport ids with engine ids — the bookkeeping every adapter needs.

A Taskport id is Taskport's. Procrastinate mints an integer, Cloud Run mints a
resource path, AWS Batch mints a UUID, and none of them will ever store ours. So
an adapter that wants ``get(execution_id)`` to work has to remember the pairing:

```mermaid
flowchart LR
    SUB["submit()"]
    IDX["ExternalIdIndex<br/>task_9f2c... ↔ 4711"]
    GET["get(task_9f2c...)"]
    ENG["engine.lookup(4711)"]

    SUB -->|"remember"| IDX
    GET -->|"resolve"| IDX --> ENG
```

Five adapters were about to grow the same ``OrderedDict`` plus lock plus eviction
loop, so it lives here once, with the two properties that matter:

**Bounded.** A producer that runs for a month must not accumulate a map for a
month. Oldest entries are evicted, and an evicted lookup fails loudly.

**Honest about its scope.** The index is per-process and in-memory. It cannot
survive a restart and it is invisible to another replica — which is a real
limitation of looking an execution up by Taskport id, not something to paper
over. Adapters accept the engine's own id as a fallback so an operator holding a
job id from a dashboard can always look it up, from anywhere.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable

DEFAULT_CAPACITY = 10_000
"""Entries kept before the oldest is evicted. Roughly 1 MB at typical id sizes."""


class ExternalIdIndex:
    """A bounded, thread-safe, bidirectional map of Taskport id ↔ engine id.

    Args:
        capacity: Maximum entries. Zero disables tracking entirely, which is a
            legitimate choice for a fire-and-forget backend that could not look
            an execution up anyway.
        recognises: Predicate deciding whether a string is already an engine id.
            It lets ``resolve`` accept ``"projects/p/locations/eu/executions/x"``
            or ``"4711"`` directly, so an id copied from a console works without
            this process ever having submitted it.
    """

    __slots__ = ("_by_execution", "_capacity", "_lock", "_recognises")

    def __init__(
        self,
        *,
        capacity: int = DEFAULT_CAPACITY,
        recognises: Callable[[str], bool] | None = None,
    ) -> None:
        if capacity < 0:
            raise ValueError("capacity must be >= 0")
        self._capacity = capacity
        self._recognises = recognises
        self._by_execution: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_execution)

    def __contains__(self, execution_id: object) -> bool:
        with self._lock:
            return str(execution_id) in self._by_execution

    def __repr__(self) -> str:
        return f"ExternalIdIndex(entries={len(self)}, capacity={self._capacity})"

    def remember(self, execution_id: str, external_id: str | None) -> None:
        """Record the pairing produced by a submission. ``None`` is ignored."""
        if external_id is None or self._capacity == 0:
            return
        with self._lock:
            self._by_execution[execution_id] = external_id
            self._by_execution.move_to_end(execution_id)
            while len(self._by_execution) > self._capacity:
                self._by_execution.popitem(last=False)

    def resolve(self, execution_id: str) -> str | None:
        """The engine id for ``execution_id``, or ``None`` when it is unknown.

        A remembered pairing wins. Failing that, ``execution_id`` is returned
        unchanged if ``recognises`` says it already *is* an engine id.
        """
        with self._lock:
            known = self._by_execution.get(execution_id)
            if known is not None:
                self._by_execution.move_to_end(execution_id)
                return known
        if self._recognises is not None and self._recognises(execution_id):
            return execution_id
        return None

    def forget(self, execution_id: str) -> None:
        """Drop one pairing. Silent when it was not tracked."""
        with self._lock:
            self._by_execution.pop(execution_id, None)

    def clear(self) -> None:
        with self._lock:
            self._by_execution.clear()


def is_digits(value: str) -> bool:
    """``recognises`` predicate for engines with numeric ids (Procrastinate)."""
    return value.isdigit()


def has_prefix(prefix: str) -> Callable[[str], bool]:
    """``recognises`` predicate for engines with path-shaped ids (Cloud Run, GCP)."""

    def _check(value: str) -> bool:
        return value.startswith(prefix)

    return _check


__all__ = ["DEFAULT_CAPACITY", "ExternalIdIndex", "has_prefix", "is_digits"]
