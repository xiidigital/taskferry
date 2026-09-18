"""Importable task functions for the test suite.

These live in a real module, at module level, on purpose: that is the only shape
a remote worker can resolve, and the tests that exercise the process pool would
fail on anything else. Which is the point — the constraint is real, and the test
suite feels it the same way a deployment does.
"""

from __future__ import annotations

import os
import time
from typing import Any

CALLS: list[Any] = []
"""Records calls made in *this* process. Empty in a child, by definition —
which several tests rely on to prove work really crossed a process boundary."""


def add(a: int, b: int) -> int:
    return a + b


def echo(value: Any = "ok", **extra: Any) -> Any:
    CALLS.append((value, extra))
    return value


def slow(seconds: float = 0.05) -> str:
    time.sleep(seconds)
    return "done"


def boom(message: str = "task failed on purpose") -> None:
    raise ValueError(message)


def pid() -> int:
    """Return the running process id, so cross-process execution is provable."""
    return os.getpid()


async def async_double(value: int) -> int:
    return value * 2


_ATTEMPTS: dict[str, int] = {}


def flaky(key: str, fail_times: int = 1) -> str:
    """Fail the first ``fail_times`` calls, then succeed. For retry tests."""
    seen = _ATTEMPTS.get(key, 0) + 1
    _ATTEMPTS[key] = seen
    if seen <= fail_times:
        raise ConnectionError(f"attempt {seen} fails")
    return f"succeeded on attempt {seen}"


def reset_attempts() -> None:
    _ATTEMPTS.clear()
    CALLS.clear()


__all__ = [
    "CALLS",
    "add",
    "async_double",
    "boom",
    "echo",
    "flaky",
    "pid",
    "reset_attempts",
    "slow",
]
