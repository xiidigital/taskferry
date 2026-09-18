"""OpenTelemetry for Taskferry — the bridge as its own distribution.

```mermaid
flowchart LR
    SPANS["taskferry.* spans<br/>enqueue · execute · submit · poll"]
    BR["taskferry_otel<br/>OTelTracer"]
    OTEL["OpenTelemetry SDK<br/>your exporters"]

    SPANS --> BR --> OTEL
```

Taskferry always *emits* spans and propagates W3C trace context; without a real
tracer installed they go to a no-op. Installing ``taskferry-otel`` and calling
:func:`configure_opentelemetry` swaps in a tracer backed by the OpenTelemetry
API, so every ``taskferry.*`` span becomes a real OTel span::

    from taskferry_otel import configure_opentelemetry

    configure_opentelemetry()          # after your TracerProvider is set up

Why a separate distribution
---------------------------

``taskferry`` declares **zero dependencies** — required or optional. The bridge
implementation lives at :mod:`taskferry.core.otel` and imports opentelemetry
lazily, so the core never carries the dependency. This package is what actually
pulls in ``opentelemetry-api`` and is the thing you install to light tracing up,
keeping the "install nothing you do not use" promise (ADR-0013) intact for
everyone who does not.
"""

from __future__ import annotations

from taskferry.core.otel import OTelTracer, configure_opentelemetry

__version__ = "0.1.0"

__all__ = ["OTelTracer", "__version__", "configure_opentelemetry"]
