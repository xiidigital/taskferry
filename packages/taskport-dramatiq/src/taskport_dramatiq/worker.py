"""The worker side — one Dramatiq actor that runs every Taskport task.

```mermaid
sequenceDiagram
    participant B as Redis / RabbitMQ
    participant W as dramatiq worker
    participant A as taskport_execute actor
    participant REG as FunctionRegistry
    participant FN as your function

    B->>W: message
    W->>A: dispatch(envelope)
    A->>REG: resolve "package.module:function"
    REG-->>A: callable
    A->>FN: call(*args, **kwargs)
```

One actor rather than one per function, for the same reason the Procrastinate
adapter does it: otherwise the worker's actor registry and the application's task
list have to be kept in sync by hand, and the set of runnable tasks becomes part
of the deployment rather than part of the code.

Retries are **Dramatiq's**. ``build_dispatch_actor(max_retries=3)`` configures
the actor and Dramatiq applies its own exponential backoff with jitter; Taskport
does not loop in the worker pretending to retry. See ADR-0019.

Security: the actor resolves a task *name* that arrived from the broker. Pass a
:class:`~taskport.functions.FunctionRegistry` with an allowlist when anything
untrusted can write to it.
"""

from __future__ import annotations

from typing import Any

from taskport.envelope import Envelope, execute_envelope
from taskport.functions import FunctionRegistry

DEFAULT_ACTOR_NAME = "taskport_execute"


def dispatch(envelope: Envelope, *, registry: FunctionRegistry | None = None) -> Any:
    """Run one Taskport envelope. Independent of Dramatiq, so it unit-tests as a dict."""
    return execute_envelope(envelope, registry=registry)


def build_dispatch_actor(
    *,
    actor_name: str = DEFAULT_ACTOR_NAME,
    registry: FunctionRegistry | None = None,
    broker: Any = None,
    **actor_options: Any,
) -> Any:
    """Build and return the Dramatiq actor that runs Taskport tasks.

    Call once in the module your worker loads, after setting a broker. Point the
    backend's ``actor`` option at the returned object or at its dotted path.

    Args:
        actor_name: Actor name. Must match the backend's ``actor_name``.
        registry: Where task names resolve. Give it an allowlist in production.
        broker: Explicit broker. Defaults to Dramatiq's global one.
        actor_options: Forwarded to ``@dramatiq.actor`` — ``max_retries``,
            ``time_limit``, ``queue_name``, ``min_backoff``, ...

    Returns:
        The registered Dramatiq actor.
    """
    import dramatiq

    if broker is not None:
        actor_options["broker"] = broker

    # The broker is deliberately typed Any: dramatiq must not be a hard
    # dependency of this package, so its decorator is untyped here.
    @dramatiq.actor(actor_name=actor_name, **actor_options)  # type: ignore[untyped-decorator]
    def taskport_execute(envelope: Envelope) -> Any:
        return dispatch(envelope, registry=registry)

    return taskport_execute


__all__ = ["DEFAULT_ACTOR_NAME", "build_dispatch_actor", "dispatch"]
