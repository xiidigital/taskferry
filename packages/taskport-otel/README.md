# taskport-otel

The **OpenTelemetry** bridge for [Taskport](https://taskport.dev). Turns the
`taskport.*` spans Taskport already emits into real OTel spans.

```bash
pip install taskport-otel
```

```python
from taskport_otel import configure_opentelemetry

# After you have configured your OpenTelemetry TracerProvider + exporters:
configure_opentelemetry()
```

That is the whole surface. Taskport instruments every enqueue, execution, job
submission and poll, and propagates W3C trace context across engine boundaries;
until you call `configure_opentelemetry`, those spans go to a no-op tracer and
cost nothing.

## Why it is a separate package

`taskport` declares **zero dependencies** — required *or* optional. The bridge
code lives at `taskport.core.otel` and imports `opentelemetry` lazily, so the
core never carries the dependency. This distribution is what pulls in
`opentelemetry-api`, so tracing is opt-in by an install, and everyone who does
not need it still installs nothing extra.

## License

Apache-2.0.
