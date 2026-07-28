"""Taskport — a portable execution layer for Python.

Taskport models units of work, picks the right kind of execution, and routes them
to engines that already exist. It is not a task queue, not a worker system, not a
scheduler and not a workflow engine. Its whole value is that your application
never has to name one.

```mermaid
flowchart LR
    APP["Application / library"]
    TP["Taskport"]
    ENGINE["Execution engine"]
    INFRA["Infrastructure"]

    APP -->|"what to run"| TP
    TP -->|"how and where"| ENGINE
    ENGINE --> INFRA
```

Three primitives, deliberately not collapsed into one:

```mermaid
flowchart TD
    TP["Taskport"]

    TP --> INLINE["Inline<br/>run it here, now"]
    TP --> TASK["Task<br/>a named function, on an engine"]
    TP --> JOB["Job<br/>a container/process, to completion"]

    TASK --> PRO["Procrastinate"]
    TASK --> CT["Cloud Tasks"]
    TASK --> CELERY["Celery · Django Tasks · ..."]

    JOB --> CR["Cloud Run Jobs"]
    JOB --> K8S["Kubernetes Jobs"]
    JOB --> LOCAL["local process"]
```

Sixty seconds in
----------------

    from taskport import Taskport

    runtime = Taskport.local()

    def add(a: int, b: int) -> int:
        return a + b

    execution = runtime.inline.submit(add, 20, 22)
    assert execution.result().value == 42

That needs no Django, no PostgreSQL, no Redis, no Procrastinate, no cloud account
and no worker process — and `import taskport` imports none of them either, ever.
Adapters are separate distributions (``taskport-procrastinate``,
``taskport-cloudrun``, ``taskport-django``, ...) that depend on Taskport, never
the other way around.

Guarantees, stated plainly
--------------------------

Taskport promises **at-least-once or at-most-once, depending on the backend**,
and never exactly-once — no distributed system can honestly offer that. Every
backend declares its real delivery semantics, and
:attr:`~taskport.specs.ExecutionSpec.idempotency_key` is offered as a tool for
building idempotency, not as a guarantee. See ADR-0018.

Everything a normal application needs is importable from here. Deeper modules
(:mod:`taskport.ports`, :mod:`taskport.contract`, :mod:`taskport.core`) are for
people writing adapters.
"""

from __future__ import annotations

from .capabilities import Capability, CapabilitySet
from .config import BackendConfig, TaskportConfig
from .core.correlation import Correlation, current_correlation, ensure_correlation, use_correlation
from .core.delivery import DeliveryGuarantee, Ordering
from .core.provider import ProviderMetadata
from .core.serialization import JsonSerializer, Serializer
from .errors import (
    BackendError,
    ConfigurationError,
    ExecutionCancelled,
    ExecutionError,
    ExecutionNotFound,
    FunctionResolutionError,
    RoutingError,
    SerializationError,
    SubmissionError,
    TaskportError,
    TaskportTimeoutError,
    UnsupportedCapability,
)
from .execution import (
    Execution,
    ExecutionId,
    ExecutionKind,
    ExecutionResult,
    ExecutionState,
)
from .functions import FunctionRef, FunctionRegistry
from .handle import ExecutionHandle
from .hooks import BaseHook, Hook, HookChain, LoggingHook
from .plugins import available_backends, register_backend, unregister_backend
from .ports import BaseBackend, ExecutionBackend, InlineBackend, JobBackend, TaskBackend
from .retry import Backoff, RetryOwner, RetryPolicy, TimeoutPolicy
from .router import Route, Router
from .runtime import Taskport
from .specs import (
    AnySpec,
    BackendOptions,
    ExecutionSpec,
    InlineSpec,
    JobSpec,
    Resources,
    TaskSpec,
)

__version__ = "0.2.0"

# Grouped by concept rather than alphabetically: this list doubles as the map of
# the public API, and "everything about executions, together" is far more useful
# to a reader than strict alphabetical order would be.
__all__ = [  # noqa: RUF022 - deliberately grouped, see above
    # runtime
    "Taskport",
    "TaskportConfig",
    "BackendConfig",
    "Route",
    "Router",
    # specs
    "AnySpec",
    "BackendOptions",
    "ExecutionSpec",
    "InlineSpec",
    "JobSpec",
    "Resources",
    "TaskSpec",
    # executions
    "Execution",
    "ExecutionHandle",
    "ExecutionId",
    "ExecutionKind",
    "ExecutionResult",
    "ExecutionState",
    # policies
    "Backoff",
    "RetryOwner",
    "RetryPolicy",
    "TimeoutPolicy",
    # capabilities
    "Capability",
    "CapabilitySet",
    # ports (for adapter authors)
    "BaseBackend",
    "ExecutionBackend",
    "InlineBackend",
    "JobBackend",
    "TaskBackend",
    # functions
    "FunctionRef",
    "FunctionRegistry",
    # observability
    "BaseHook",
    "Correlation",
    "Hook",
    "HookChain",
    "LoggingHook",
    "current_correlation",
    "ensure_correlation",
    "use_correlation",
    # semantics + serialization
    "DeliveryGuarantee",
    "JsonSerializer",
    "Ordering",
    "ProviderMetadata",
    "Serializer",
    # plugins
    "available_backends",
    "register_backend",
    "unregister_backend",
    # errors
    "BackendError",
    "ConfigurationError",
    "ExecutionCancelled",
    "ExecutionError",
    "ExecutionNotFound",
    "FunctionResolutionError",
    "RoutingError",
    "SerializationError",
    "SubmissionError",
    "TaskportError",
    "TaskportTimeoutError",
    "UnsupportedCapability",
    "__version__",
]
