"""Observability hooks — designed in from v1, mandatory on no one (ADR-0012).

Taskferry propagates trace context and correlation and emits spans at consistent
points, but it does **not** depend on the OpenTelemetry SDK. Out of the box a
:class:`NoopTracer` is used; installing the ``otel`` extra and calling
:func:`set_tracer` with an OTel-backed tracer lights everything up.

Span names and attribute keys are centralized here so every package
(``taskferry.task.enqueue``, ``taskferry.job.submit`` ...) stays coherent
(section 47).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from types import TracebackType
from typing import Protocol, runtime_checkable

# --- Semantic span names (section 47) -------------------------------------- #
SPAN_TASK_ENQUEUE = "taskferry.task.enqueue"
SPAN_TASK_EXECUTE = "taskferry.task.execute"
SPAN_JOB_SUBMIT = "taskferry.job.submit"
SPAN_JOB_POLL = "taskferry.job.poll"
SPAN_EVENT_PUBLISH = "taskferry.event.publish"
SPAN_EVENT_CONSUME = "taskferry.event.consume"
SPAN_SCHEDULE_CREATE = "taskferry.schedule.create"

# --- Semantic attribute keys (section 18) ---------------------------------- #
ATTR_PROVIDER = "taskferry.provider"
ATTR_TASK_ID = "taskferry.task_id"
ATTR_JOB_ID = "taskferry.job_id"
ATTR_EVENT_ID = "taskferry.event_id"
ATTR_SCHEDULE_ID = "taskferry.schedule_id"
ATTR_CORRELATION_ID = "taskferry.correlation_id"
ATTR_ATTEMPT = "taskferry.attempt"
ATTR_PROVIDER_ID = "taskferry.provider_id"

AttributeValue = str | int | float | bool
Attributes = Mapping[str, AttributeValue]


@runtime_checkable
class Span(Protocol):
    """Minimal span surface adapters use inside a ``with`` block."""

    def set_attribute(self, key: str, value: AttributeValue) -> None: ...

    def record_exception(self, exc: BaseException) -> None: ...

    def __enter__(self) -> Span: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


@runtime_checkable
class Tracer(Protocol):
    """A factory of spans. The OTel bridge and the no-op both satisfy this."""

    def start_span(self, name: str, attributes: Attributes | None = None) -> Span: ...


class _NoopSpan:
    __slots__ = ()

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        return None

    def record_exception(self, exc: BaseException) -> None:
        return None

    def __enter__(self) -> _NoopSpan:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class NoopTracer:
    """Default tracer: correct, cheap, does nothing."""

    def start_span(self, name: str, attributes: Attributes | None = None) -> Span:
        return _NoopSpan()


_tracer: Tracer = NoopTracer()


def set_tracer(tracer: Tracer) -> None:
    """Install the process-wide tracer (e.g. an OpenTelemetry-backed one)."""
    global _tracer
    _tracer = tracer


def get_tracer() -> Tracer:
    """Return the process-wide tracer (a :class:`NoopTracer` until set)."""
    return _tracer


@contextmanager
def span(name: str, attributes: Attributes | None = None) -> Iterator[Span]:
    """Convenience wrapper: ``with span("taskferry.job.submit", {...}) as s:``."""
    started = get_tracer().start_span(name, attributes)
    with started as active:
        yield active


__all__ = [
    "ATTR_ATTEMPT",
    "ATTR_CORRELATION_ID",
    "ATTR_EVENT_ID",
    "ATTR_JOB_ID",
    "ATTR_PROVIDER",
    "ATTR_PROVIDER_ID",
    "ATTR_SCHEDULE_ID",
    "ATTR_TASK_ID",
    "SPAN_EVENT_CONSUME",
    "SPAN_EVENT_PUBLISH",
    "SPAN_JOB_POLL",
    "SPAN_JOB_SUBMIT",
    "SPAN_SCHEDULE_CREATE",
    "SPAN_TASK_ENQUEUE",
    "SPAN_TASK_EXECUTE",
    "Attributes",
    "NoopTracer",
    "Span",
    "Tracer",
    "get_tracer",
    "set_tracer",
    "span",
]
