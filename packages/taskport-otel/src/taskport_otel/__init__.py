"""OpenTelemetry for Taskport — the bridge as its own distribution.

```mermaid
flowchart LR
    SPANS["taskport.* spans<br/>enqueue · execute · submit · poll"]
    BR["taskport_otel<br/>OTelTracer"]
    OTEL["OpenTelemetry SDK<br/>your exporters"]

    SPANS --> BR --> OTEL
```

Taskport always *emits* spans and propagates W3C trace context; without a real
tracer installed they go to a no-op. Installing ``taskport-otel`` and calling
:func:`configure_opentelemetry` swaps in a tracer backed by the OpenTelemetry
API, so every ``taskport.*`` span becomes a real OTel span::

    from taskport_otel import configure_opentelemetry

    configure_opentelemetry()          # after your TracerProvider is set up

Why a separate distribution
---------------------------

``taskport`` declares **zero dependencies** — required or optional. The bridge
implementation lives at :mod:`taskport.core.otel` and imports opentelemetry
lazily, so the core never carries the dependency. This package is what actually
pulls in ``opentelemetry-api`` and is the thing you install to light tracing up,
keeping the "install nothing you do not use" promise (ADR-0013) intact for
everyone who does not.
"""

from __future__ import annotations

from taskport.core.otel import OTelTracer, configure_opentelemetry

__version__ = "0.1.0"

__all__ = ["OTelTracer", "__version__", "configure_opentelemetry"]
