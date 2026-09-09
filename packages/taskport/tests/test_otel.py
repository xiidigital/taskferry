"""Tests for the OpenTelemetry bridge using an in-memory span exporter."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from taskport.core import (
    NoopTracer,
    configure_opentelemetry,
    get_tracer,
    set_tracer,
    span,
)
from taskport.core.observability import ATTR_PROVIDER, SPAN_JOB_SUBMIT

trace = pytest.importorskip("opentelemetry.trace")
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)


@pytest.fixture(scope="module")
def exporter() -> InMemorySpanExporter:
    # The OTel global TracerProvider may only be *set* once per process, but
    # processors can always be added. Reuse whatever provider is active (another
    # test module may have set it first) and attach our own exporter to it, so
    # this fixture is order-independent.
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
    configure_opentelemetry("taskport-test")
    try:
        yield exporter
    finally:
        set_tracer(original)


def test_configure_opentelemetry_emits_real_spans(bridged: InMemorySpanExporter) -> None:
    with span(SPAN_JOB_SUBMIT, {ATTR_PROVIDER: "local"}) as active:
        active.set_attribute("taskport.job_id", "job_123")

    finished = bridged.get_finished_spans()
    assert len(finished) == 1
    emitted = finished[0]
    assert emitted.name == SPAN_JOB_SUBMIT
    assert emitted.attributes[ATTR_PROVIDER] == "local"
    assert emitted.attributes["taskport.job_id"] == "job_123"


def test_configure_returns_tracer_and_restores(exporter: InMemorySpanExporter) -> None:
    original = get_tracer()
    try:
        tracer = configure_opentelemetry()
        assert get_tracer() is tracer
    finally:
        set_tracer(original)
    assert isinstance(get_tracer(), NoopTracer)


def test_bridge_records_exception(bridged: InMemorySpanExporter) -> None:
    with pytest.raises(ValueError), span("taskport.task.execute"):
        raise ValueError("boom")

    finished = bridged.get_finished_spans()
    assert finished
    events = [event for s in finished for event in s.events]
    assert any(event.name == "exception" for event in events)
