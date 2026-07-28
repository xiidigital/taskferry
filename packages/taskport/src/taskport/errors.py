"""The portable Taskport error hierarchy.

Every error a caller can reasonably catch is a :class:`TaskportError`. Adapters
translate provider exceptions into these types and **always** chain the original
as ``__cause__``::

    raise SubmissionError("defer failed", backend="procrastinate") from exc

Provider exceptions (``google.api_core.GoogleAPIError``, ``psycopg.Error``,
``kubernetes.client.ApiException`` ...) are never part of Taskport's public API,
so application code never has to import a provider SDK to handle a failure.

```mermaid
flowchart TD
    E["TaskportError"]
    E --> C["ConfigurationError"]
    E --> B["BackendError"]
    B --> S["SubmissionError"]
    B --> X["ExecutionError"]
    B --> NF["ExecutionNotFound"]
    E --> U["UnsupportedCapability"]
    E --> SER["SerializationError"]
    E --> T["TimeoutError"]
    E --> R["RoutingError"]
    E --> F["FunctionResolutionError"]
    E --> CAN["ExecutionCancelled"]
```
"""

from __future__ import annotations

from taskport.core.errors import ConfigurationError, SerializationError, TaskportError
from taskport.core.errors import ProviderError as _ProviderError
from taskport.core.errors import UnsupportedCapabilityError as _UnsupportedCapabilityError


class BackendError(_ProviderError):
    """A backend failed to carry out an operation.

    Subclasses ``ProviderError`` so code written against 0.1 keeps working. The
    offending backend is named in :attr:`backend`.
    """

    def __init__(self, message: str, *, backend: str | None = None) -> None:
        super().__init__(message, provider=backend)

    @property
    def backend(self) -> str | None:
        """Name of the backend that raised, when known."""
        return self.provider


class SubmissionError(BackendError):
    """A spec could not be handed to the backend.

    Raised at submit time — nothing was enqueued, so retrying the submission is
    safe unless the backend documents otherwise.
    """


class ExecutionError(BackendError):
    """An execution ran and failed.

    :attr:`cause_repr` carries the remote exception's textual form when the
    backend can supply it (the real exception object rarely survives a process or
    network boundary).
    """

    def __init__(
        self,
        message: str,
        *,
        backend: str | None = None,
        cause_repr: str | None = None,
    ) -> None:
        super().__init__(message, backend=backend)
        self.cause_repr = cause_repr


class ExecutionNotFound(BackendError):
    """No execution with the given id is known to the backend.

    Distinct from :class:`ExecutionError`: the execution may never have existed,
    or the backend may have expired its record.
    """


class ExecutionCancelled(BackendError):
    """The execution was cancelled before it could produce a result."""


UnsupportedCapability = _UnsupportedCapabilityError
"""The operation needs a capability the backend does not advertise.

Taskport raises this rather than emulating the capability, because an emulated
cancel or an emulated timeout is a correctness bug waiting for production.

This is an **alias**, not a subclass, of
:class:`taskport.core.errors.UnsupportedCapabilityError`. It has to be: a
subclass would mean ``CapabilitySet.require()`` raises the parent while every
caller is told to catch the child, so the documented ``except
UnsupportedCapability`` would silently miss the most common source of the error.
One concept, one class, two names."""


class RoutingError(ConfigurationError):
    """No backend could be selected for a spec, or the selected one is unknown."""


class FunctionResolutionError(TaskportError):
    """A ``package.module:function`` reference could not be resolved.

    Also raised when a reference is rejected by the import allowlist — the
    message says which, so a deployment problem is never mistaken for a typo.
    """


class TaskportTimeoutError(TaskportError, TimeoutError):
    """A ``wait``/``result`` call exceeded its timeout.

    Subclasses the built-in :class:`TimeoutError` so ``except TimeoutError``
    keeps working for callers that do not import Taskport's hierarchy.
    """


# Public alias. ``taskport.TimeoutError`` shadows the builtin only inside an
# explicit ``from taskport import TimeoutError``, which is opt-in and explicit.
TimeoutError = TaskportTimeoutError

__all__ = [
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
    "TimeoutError",
    "UnsupportedCapability",
]
