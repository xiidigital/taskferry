"""Retry intent — and, more importantly, who owns it.

Retries stack badly. If the application retries, and Taskport retries, and the
engine retries, and the platform retries, five attempts become several hundred:

```mermaid
flowchart TD
    APP["Application"]
    TP["Taskport"]
    ENGINE["Engine"]
    INFRA["Infrastructure"]

    APP -. "retry?" .-> TP
    TP -. "retry?" .-> ENGINE
    ENGINE -. "retry?" .-> INFRA
```

Taskport's rule: **Taskport never retries.** A :class:`RetryPolicy` is a portable
*declaration of intent* that a backend translates into its engine's native retry
configuration. :class:`RetryOwner` records who is actually going to act on it, so
the answer is written down rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Backoff(StrEnum):
    """How the delay between attempts grows."""

    NONE = "none"
    """Retry immediately."""

    FIXED = "fixed"
    """Always wait ``initial_delay``."""

    LINEAR = "linear"
    """Wait ``initial_delay * attempt``."""

    EXPONENTIAL = "exponential"
    """Wait ``initial_delay * 2 ** (attempt - 1)``."""


class RetryOwner(StrEnum):
    """Which layer performs the retry. Exactly one layer should."""

    BACKEND = "backend"
    """The engine retries natively (Procrastinate, Cloud Tasks, Cloud Run,
    Kubernetes). Taskport translates the policy and then stays out of the way.
    This is the default and the recommended value."""

    APPLICATION = "application"
    """The caller retries. Taskport passes ``max_attempts=1`` to the engine so
    the engine does not retry as well."""

    NONE = "none"
    """Nobody retries. A failure is final."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Portable retry intent.

    Attributes:
        max_attempts: Total attempts including the first. ``1`` disables retries.
        backoff: Growth strategy for the delay between attempts.
        initial_delay: Seconds before the second attempt.
        max_delay: Ceiling for a computed delay, or ``None`` for no ceiling.
        jitter: Ask the engine to randomise the delay. Engines that cannot do
            this ignore the flag — it is a hint, not a capability requirement.
        retry_on: Exception type names (``"ConnectionError"``,
            ``"myapp.errors.Transient"``) that *should* be retried. Empty means
            "retry anything".
        no_retry_on: Exception type names that must never be retried. Takes
            precedence over :attr:`retry_on`.
        owner: Who performs the retry. See :class:`RetryOwner`.

    ``retry_on``/``no_retry_on`` are advisory: a backend that cannot filter by
    exception type advertises no such capability and ignores them. The contract
    suite asserts the backend says so rather than pretending.
    """

    max_attempts: int = 1
    backoff: Backoff = Backoff.EXPONENTIAL
    initial_delay: float = 1.0
    max_delay: float | None = 300.0
    jitter: bool = True
    retry_on: tuple[str, ...] = ()
    no_retry_on: tuple[str, ...] = ()
    owner: RetryOwner = RetryOwner.BACKEND

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.initial_delay < 0:
            raise ValueError("initial_delay must be >= 0")
        if self.max_delay is not None and self.max_delay < 0:
            raise ValueError("max_delay must be >= 0 when set")

    @property
    def enabled(self) -> bool:
        """Whether this policy asks for any retry at all."""
        return self.max_attempts > 1 and self.owner is not RetryOwner.NONE

    @property
    def engine_attempts(self) -> int:
        """Attempts to configure on the engine.

        ``1`` unless the engine owns the retry — this is what stops two layers
        from multiplying their attempt counts.
        """
        return self.max_attempts if self.owner is RetryOwner.BACKEND else 1

    def delay_for(self, attempt: int) -> float:
        """Delay in seconds before ``attempt`` (1-based; ``delay_for(1)`` is 0).

        Pure and deterministic — :attr:`jitter` is applied by the engine, not
        here, so this stays testable.
        """
        if attempt <= 1:
            return 0.0
        match self.backoff:
            case Backoff.NONE:
                delay = 0.0
            case Backoff.FIXED:
                delay = self.initial_delay
            case Backoff.LINEAR:
                delay = self.initial_delay * (attempt - 1)
            case Backoff.EXPONENTIAL:
                delay = self.initial_delay * (2 ** (attempt - 2))
        if self.max_delay is not None:
            delay = min(delay, self.max_delay)
        return delay

    def should_retry(self, exc: BaseException, attempt: int) -> bool:
        """Whether ``exc`` on ``attempt`` warrants another try.

        Only backends that execute in-process (inline, local) can call this;
        remote engines apply their own equivalent from the translated policy.
        """
        if attempt >= self.max_attempts or self.owner is not RetryOwner.BACKEND:
            return False
        names = _type_names(exc)
        if self.no_retry_on and names & set(self.no_retry_on):
            return False
        if self.retry_on:
            return bool(names & set(self.retry_on))
        return True


def _type_names(exc: BaseException) -> set[str]:
    """Every name ``exc`` can be matched by: bare and fully qualified, per MRO."""
    names: set[str] = set()
    for klass in type(exc).__mro__:
        if klass is object:
            break
        names.add(klass.__name__)
        names.add(f"{klass.__module__}.{klass.__qualname__}")
    return names


NO_RETRY = RetryPolicy(max_attempts=1, owner=RetryOwner.NONE)
"""Shared instance for "this must not be retried"."""


@dataclass(frozen=True, slots=True)
class TimeoutPolicy:
    """Portable wall-clock timeout intent.

    Attributes:
        seconds: Wall-clock budget, or ``None`` for no limit.
        cancel_on_timeout: Ask the engine to cancel the execution when the
            budget is exhausted rather than letting it run on.

    A backend that cannot enforce timeouts does not advertise
    :attr:`~taskport.capabilities.Capability.TIMEOUT`, and a spec carrying one is
    rejected — an unenforced timeout is worse than no timeout.
    """

    seconds: float | None = None
    cancel_on_timeout: bool = True

    def __post_init__(self) -> None:
        if self.seconds is not None and self.seconds <= 0:
            raise ValueError("timeout seconds must be positive when set")

    @property
    def enabled(self) -> bool:
        return self.seconds is not None


NO_TIMEOUT = TimeoutPolicy()
"""Shared instance for "no wall-clock limit"."""


__all__ = [
    "NO_RETRY",
    "NO_TIMEOUT",
    "Backoff",
    "RetryOwner",
    "RetryPolicy",
    "TimeoutPolicy",
]
