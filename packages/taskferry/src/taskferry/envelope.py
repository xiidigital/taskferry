"""The portable task envelope — one wire format, every transport.

```mermaid
flowchart LR
    SPEC["TaskSpec"]
    ENV["envelope<br/>{taskferry, task, args, kwargs, correlation, retry}"]

    ENV --> PG["Procrastinate job argument"]
    ENV --> CT["Cloud Tasks HTTP body"]
    ENV --> SQS["SQS message body"]
    ENV --> SB["Service Bus message body"]
    ENV --> DQ["Dramatiq actor argument"]

    SPEC --> ENV
```

Five adapters were about to grow the same twenty lines — build a dict, read the
correlation back, check the version, encode the retry policy. So it lives here
once, and every transport that can carry a JSON object gets a consistent,
inspectable payload.

What it is not
--------------

This is **not** a queue protocol. It carries no routing, no priority, no
scheduling and no delivery metadata, because every engine has its own and
better mechanisms for those — a `queueing_lock`, a `schedule_time`, a
`MessageGroupId`. The envelope carries only what the *worker* needs in order to
run the right function with the right arguments. Anything the engine can express
natively is translated by the adapter, not smuggled through here.

Two rules
---------

**JSON only, never pickle.** A payload sits in a PostgreSQL table or an SQS queue
where an operator can read it, survives a Python upgrade, and cannot become
arbitrary code execution on the way back in. See
[ADR-0009](../../../docs/adr/0009-serialization.md).

**Version it, and reject what you do not know.** A worker running old code must
not silently drop a field a newer producer added. :func:`read_envelope` refuses
an unfamiliar version rather than guessing.
"""

from __future__ import annotations

from typing import Any, TypedDict

from .core.correlation import Correlation
from .core.typing import JSONValue
from .errors import SerializationError
from .retry import NO_RETRY, Backoff, RetryPolicy
from .specs import TaskSpec

ENVELOPE_VERSION = "2"
"""Bumped when the shape changes. Workers reject versions they do not know."""


class Envelope(TypedDict, total=False):
    """The on-the-wire shape of a task."""

    taskferry: str
    task: str
    args: list[JSONValue]
    kwargs: dict[str, JSONValue]
    name: str
    queue: str
    correlation: dict[str, str] | None
    labels: dict[str, str]
    retry: dict[str, JSONValue] | None


def build_envelope(spec: TaskSpec) -> Envelope:
    """Turn a :class:`~taskferry.specs.TaskSpec` into the wire envelope.

    Arguments were validated as JSON-shaped when the spec was constructed, so
    nothing can fail here that would not already have failed at the call site.
    """
    return {
        "taskferry": ENVELOPE_VERSION,
        "task": spec.task,
        "args": list(spec.args),
        "kwargs": dict(spec.kwargs),
        "name": spec.name,
        "queue": spec.queue,
        "correlation": spec.correlation.to_headers() if spec.correlation else None,
        "labels": dict(spec.labels),
        "retry": encode_retry(spec.retry),
    }


def read_envelope(payload: Any) -> Envelope:
    """Validate an incoming payload and return it as an :class:`Envelope`.

    Raises:
        SerializationError: when the payload is not an envelope of a version this
            code understands, or carries no task. Failing loudly is deliberate: a
            malformed body means a misconfigured endpoint or a version skew, and
            quietly doing nothing would hide both.
    """
    if not isinstance(payload, dict):
        raise SerializationError(
            f"a taskferry envelope must be a JSON object, got {type(payload).__name__}"
        )
    version = payload.get("taskferry")
    if version != ENVELOPE_VERSION:
        raise SerializationError(
            f"unsupported taskferry envelope version {version!r} "
            f"(this worker speaks {ENVELOPE_VERSION!r}); upgrade the worker or the producer"
        )
    if not payload.get("task"):
        raise SerializationError("taskferry envelope has no 'task'")
    return payload  # type: ignore[return-value]


def read_correlation(envelope: Envelope) -> Correlation:
    """Rebuild the correlation, starting a fresh flow when the envelope has none."""
    headers = envelope.get("correlation")
    return Correlation.from_headers(headers) if headers else Correlation.start()


def encode_retry(policy: RetryPolicy) -> dict[str, JSONValue] | None:
    """Serialize the portable retry intent, or ``None`` when nothing is asked for.

    Only what a worker needs to compute the *next* delay travels. The policy
    object itself is not shipped, because a queue payload must not depend on the
    producer and the consumer running the same Taskferry version.
    """
    if not policy.enabled:
        return None
    return {
        "max_attempts": policy.max_attempts,
        "backoff": policy.backoff.value,
        "initial_delay": policy.initial_delay,
        "max_delay": policy.max_delay,
        "retry_on": list(policy.retry_on),
        "no_retry_on": list(policy.no_retry_on),
    }


def decode_retry(envelope: Envelope) -> RetryPolicy:
    """Rebuild the policy on the worker side. Unknown fields are ignored."""
    raw = envelope.get("retry")
    if not raw:
        return NO_RETRY
    return RetryPolicy(
        max_attempts=int(raw.get("max_attempts", 1) or 1),  # type: ignore[arg-type]
        backoff=Backoff(str(raw.get("backoff", Backoff.EXPONENTIAL.value))),
        initial_delay=float(raw.get("initial_delay", 1.0) or 0.0),  # type: ignore[arg-type]
        max_delay=(
            None if raw.get("max_delay") is None else float(raw["max_delay"])  # type: ignore[arg-type]
        ),
        retry_on=tuple(str(name) for name in (raw.get("retry_on") or [])),  # type: ignore[union-attr]
        no_retry_on=tuple(str(name) for name in (raw.get("no_retry_on") or [])),  # type: ignore[union-attr]
    )


def execute_envelope(payload: Any, *, registry: Any = None) -> Any:
    """Validate, resolve and run one envelope. The whole worker side, in one call.

    Args:
        payload: The decoded JSON body.
        registry: A :class:`~taskferry.functions.FunctionRegistry`. **Give it an
            allowlist** when envelopes arrive from a queue anything untrusted can
            write to — resolving a name means importing a module. See
            ``docs/security.md``.

    Returns:
        Whatever the task returned. Most engines discard it.
    """
    from .core.correlation import use_correlation
    from .functions import FunctionRegistry, is_async_callable

    envelope = read_envelope(payload)
    resolver = registry if registry is not None else FunctionRegistry()
    func = resolver.resolve(str(envelope["task"]))
    args = list(envelope.get("args") or [])
    kwargs = dict(envelope.get("kwargs") or {})

    with use_correlation(read_correlation(envelope)):
        if is_async_callable(func):
            import asyncio

            return asyncio.run(func(*args, **kwargs))
        return func(*args, **kwargs)


__all__ = [
    "ENVELOPE_VERSION",
    "Envelope",
    "build_envelope",
    "decode_retry",
    "encode_retry",
    "execute_envelope",
    "read_correlation",
    "read_envelope",
]
