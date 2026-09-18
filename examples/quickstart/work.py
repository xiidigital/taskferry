"""Task functions live in an importable module, not in ``__main__``.

That is not a Taskferry quirk — it is what every task engine requires. A worker in
another process imports the function by name, and ``__main__`` means something
different there. Taskferry enforces it at submit time so the failure happens in
your terminal rather than on a worker at 3am.

Inline execution is the exception: it takes any callable, including a lambda,
because nothing crosses a process boundary.
"""

from __future__ import annotations


def add(a: int, b: int) -> int:
    return a + b
