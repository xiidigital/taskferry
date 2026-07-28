"""The portable envelope that travels through Procrastinate.

One Procrastinate task (`taskport:dispatch`) carries every Taskport task. The
alternative — registering one Procrastinate task per Python function — would
mean the worker's registry and the application's task list have to be kept in
sync by hand, and it would make the set of runnable tasks part of the deployment
rather than part of the code.

The envelope is JSON, always:

```mermaid
flowchart LR
    SPEC["TaskSpec"]
    MSG["{taskport, task, args, kwargs, correlation}"]
    JOB["procrastinate job"]
    WORKER["worker"]
    FN["package.module:function"]

    SPEC --> MSG --> JOB --> WORKER --> FN
```

No pickle: an unpickle of queue contents is arbitrary code execution, and a
pickled payload also ties the queue to one interpreter version. A name plus JSON
is inspectable in ``psql``, survives a Python upgrade, and can be read by a
worker written in another language if it ever needs to be.
"""

from __future__ import annotations

from typing import Any, TypedDict

from taskport.core.correlation import Correlation
from taskport.core.typing import JSONValue
from taskport.retry import NO_RETRY, Backoff, RetryPolicy
from taskport.specs import TaskSpec

MESSAGE_VERSION = "2"
"""Bumped when the envelope changes shape. Workers reject versions they do not know."""


class TaskportMessage(TypedDict, total=False):
    """What a Procrastinate job argument looks like."""

    taskport: str
    task: str
    args: list[JSONValue]
    kwargs: dict[str, JSONValue]
    name: str
    queue: str
    correlation: dict[str, str] | None
    labels: dict[str, str]
    retry: dict[str, JSONValue] | None


def build_message(spec: TaskSpec) -> TaskportMessage:
    """Turn a :class:`~taskport.specs.TaskSpec` into the wire envelope.

    Arguments were already validated as JSON-shaped when the spec was built, so
    nothing can fail here that would not have failed at the call site.
    """
    return {
        "taskport": MESSAGE_VERSION,
        "task": spec.task,
        "args": list(spec.args),
        "kwargs": dict(spec.kwargs),
        "name": spec.name,
        "queue": spec.queue,
        "correlation": spec.correlation.to_headers() if spec.correlation else None,
        "labels": dict(spec.labels),
        "retry": encode_retry(spec.retry),
    }


def encode_retry(policy: RetryPolicy) -> dict[str, JSONValue] | None:
    """Serialize the portable retry intent, or ``None`` when nothing is asked for.

    Only what the worker needs to compute the *next* delay travels — the policy
    object itself is not shipped, because a queue payload must not depend on the
    producer and the consumer running the same Taskport version.
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


def decode_retry(message: TaskportMessage) -> RetryPolicy:
    """Rebuild the policy on the worker side. Unknown fields are ignored."""
    raw = message.get("retry")
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


def read_correlation(message: TaskportMessage) -> Correlation:
    """Rebuild the correlation from a message, starting a fresh flow if absent."""
    headers = message.get("correlation")
    return Correlation.from_headers(headers) if headers else Correlation.start()


def check_version(message: Any) -> None:
    """Reject an envelope this worker does not understand.

    Failing loudly beats guessing: a worker running old code that silently
    dropped new fields would produce results that look right and are not.
    """
    if not isinstance(message, dict):
        raise ValueError(f"taskport message must be a JSON object, got {type(message).__name__}")
    version = message.get("taskport")
    if version != MESSAGE_VERSION:
        raise ValueError(
            f"unsupported taskport message version {version!r} "
            f"(this worker speaks {MESSAGE_VERSION!r}); upgrade the worker or the producer"
        )


__all__ = [
    "MESSAGE_VERSION",
    "TaskportMessage",
    "build_message",
    "check_version",
    "decode_retry",
    "encode_retry",
    "read_correlation",
]
