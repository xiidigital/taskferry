"""OpenTelemetry bridge (ADR-0012).

Installing the ``otel`` extra and calling :func:`configure_opentelemetry` swaps
the process-wide :class:`~taskport.core.observability.NoopTracer` for one backed
by the OpenTelemetry API, so every ``taskport.*`` span becomes a real OTel span.

OpenTelemetry is imported lazily here, so ``import taskport.core`` (which imports
this module for the public re-exports) never requires the OTel packages — only
calling :func:`configure_opentelemetry` does.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

from .observability import Attributes, Span, Tracer, set_tracer


class _OTelSpan:
    """Adapts an OpenTelemetry ``start_as_current_span`` context manager to :class:`Span`."""

    __slots__ = ("_cm", "_span")

    def __init__(self, cm: Any) -> None:
        self._cm = cm
        self._span: Any = None

    def __enter__(self) -> _OTelSpan:
        self._span = self._cm.__enter__()
        return self

    def set_attribute(self, key: str, value: object) -> None:
        if self._span is not None:
            self._span.set_attribute(key, value)

    def record_exception(self, exc: BaseException) -> None:
        if self._span is not None:
            self._span.record_exception(exc)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc is not None and self._span is not None:
            self._span.record_exception(exc)
        self._cm.__exit__(exc_type, exc, tb)


class OTelTracer:
    """A :class:`Tracer` backed by an OpenTelemetry tracer."""

    def __init__(self, otel_tracer: Any) -> None:
        self._tracer = otel_tracer

    def start_span(self, name: str, attributes: Attributes | None = None) -> Span:
        cm = self._tracer.start_as_current_span(
            name, attributes=dict(attributes) if attributes else None
        )
        return _OTelSpan(cm)


def configure_opentelemetry(instrumenting_module_name: str = "taskport") -> Tracer:
    """Install an OpenTelemetry-backed tracer process-wide and return it.

    Requires the ``otel`` extra (``opentelemetry-api``). The application is still
    responsible for configuring the OTel ``TracerProvider`` and exporters.
    """
    try:
        from opentelemetry import trace
    except ImportError as exc:  # pragma: no cover - env-specific
        raise RuntimeError(
            "OpenTelemetry is required for configure_opentelemetry; "
            "install taskport-otel (which pulls in opentelemetry-api)"
        ) from exc
    tracer = OTelTracer(trace.get_tracer(instrumenting_module_name))
    set_tracer(tracer)
    return tracer


__all__ = ["OTelTracer", "configure_opentelemetry"]
