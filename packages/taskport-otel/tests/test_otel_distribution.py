"""taskport-otel: the bridge, exercised through the distribution's own surface.

Mirrors the core bridge test but imports from ``taskport_otel``, proving the
distribution re-exports a working bridge and that the OTel dependency lives here.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from taskport.core import NoopTracer, get_tracer, set_tracer, span
from taskport.core.observability import ATTR_PROVIDER, SPAN_JOB_SUBMIT
from taskport_otel import OTelTracer, configure_opentelemetry

trace = pytest.importorskip("opentelemetry.trace")
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)


def test_the_distribution_reexports_the_bridge() -> None:
    assert configure_opentelemetry.__module__ == "taskport.core.otel"
    assert OTelTracer.__module__ == "taskport.core.otel"


@pytest.fixture(scope="module")
def exporter() -> InMemorySpanExporter:
    # Order-independent: the global provider may only be set once, but a
    # processor can always be attached to whichever provider is active.
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    exp = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    return exp


@pytest.fixture
def bridged(exporter: InMemorySpanExporter) -> Iterator[InMemorySpanExporter]:
    exporter.clear()
    original = get_tracer()
    configure_opentelemetry("taskport-otel-test")
    try:
        yield exporter
    finally:
        set_tracer(original)


def test_spans_reach_opentelemetry(bridged: InMemorySpanExporter) -> None:
    with span(SPAN_JOB_SUBMIT, {ATTR_PROVIDER: "celery"}) as active:
        active.set_attribute("taskport.job_id", "job_1")

    finished = bridged.get_finished_spans()
    assert len(finished) == 1
    assert finished[0].name == SPAN_JOB_SUBMIT
    assert finished[0].attributes[ATTR_PROVIDER] == "celery"


def test_configure_restores_to_noop(exporter: InMemorySpanExporter) -> None:
    original = get_tracer()
    try:
        assert configure_opentelemetry() is get_tracer()
    finally:
        set_tracer(original)
    assert isinstance(get_tracer(), NoopTracer)
