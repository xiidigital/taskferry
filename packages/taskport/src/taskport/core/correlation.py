"""Correlation metadata for following a logical flow across boundaries.

Lets you determine that an HTTP request → Task → Event → Job all belong to the
same logical flow (section 19) **without** turning Taskport into a workflow
engine. It is just metadata that adapters propagate.

The ``trace_context`` mapping carries W3C Trace Context (``traceparent`` /
``tracestate``) so distributed tracing works even without the OpenTelemetry SDK
installed (ADR-0012). Taskport only moves the strings around.
"""

from __future__ import annotations

import contextvars
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from .ids import TaskportId, new_id


@dataclass(frozen=True, slots=True)
class Correlation:
    """Immutable correlation envelope propagated with every operation.

    Attributes:
        correlation_id: Stable id shared by every step of one logical flow.
        causation_id: Id of the *immediate* cause (the step that triggered this
            one). ``None`` at the root of a flow.
        trace_context: W3C Trace Context carrier (``traceparent``/``tracestate``).
    """

    correlation_id: TaskportId
    causation_id: TaskportId | None = None
    trace_context: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "trace_context", MappingProxyType(dict(self.trace_context)))

    @classmethod
    def start(cls) -> Correlation:
        """Begin a fresh flow with a new correlation id and no cause."""
        return cls(correlation_id=new_id("corr"))

    def caused(self, cause_id: TaskportId) -> Correlation:
        """Return a child correlation for a step caused by ``cause_id``.

        The correlation id is preserved (same flow); the causation id advances.
        """
        return replace(self, causation_id=cause_id)

    def to_headers(self) -> dict[str, str]:
        """Serialize to portable transport headers/attributes."""
        headers = {
            "taskport-correlation-id": self.correlation_id,
            **dict(self.trace_context),
        }
        if self.causation_id is not None:
            headers["taskport-causation-id"] = self.causation_id
        return headers

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> Correlation:
        """Reconstruct correlation from transport headers/attributes.

        Falls back to starting a fresh flow when no correlation id is present.
        """
        correlation_id = headers.get("taskport-correlation-id")
        if not correlation_id:
            return cls.start()
        trace_context = {k: v for k, v in headers.items() if k in ("traceparent", "tracestate")}
        causation = headers.get("taskport-causation-id")
        return cls(
            correlation_id=TaskportId(correlation_id),
            causation_id=TaskportId(causation) if causation else None,
            trace_context=trace_context,
        )


_current: contextvars.ContextVar[Correlation | None] = contextvars.ContextVar(
    "taskport_correlation", default=None
)


def current_correlation() -> Correlation | None:
    """Return the correlation bound to the current context, if any."""
    return _current.get()


def ensure_correlation() -> Correlation:
    """Return the current correlation, starting a fresh flow if none is bound."""
    existing = _current.get()
    return existing if existing is not None else Correlation.start()


class use_correlation:
    """Context manager binding ``correlation`` to the current execution context.

    Restores the previous value on exit, so it nests cleanly.
    """

    def __init__(self, correlation: Correlation) -> None:
        self._correlation = correlation
        self._token: contextvars.Token[Correlation | None] | None = None

    def __enter__(self) -> Correlation:
        self._token = _current.set(self._correlation)
        return self._correlation

    def __exit__(self, *exc: object) -> None:
        assert self._token is not None
        _current.reset(self._token)


__all__ = [
    "Correlation",
    "current_correlation",
    "ensure_correlation",
    "use_correlation",
]
