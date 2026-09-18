# taskferry-otel

The **OpenTelemetry** bridge for [Taskferry](https://taskferry.dev). Turns the
`taskferry.*` spans Taskferry already emits into real OTel spans.

```bash
pip install taskferry-otel
```

```python
from taskferry_otel import configure_opentelemetry

# After you have configured your OpenTelemetry TracerProvider + exporters:
configure_opentelemetry()
```

That is the whole surface. Taskferry instruments every enqueue, execution, job
submission and poll, and propagates W3C trace context across engine boundaries;
until you call `configure_opentelemetry`, those spans go to a no-op tracer and
cost nothing.

## Why it is a separate package

`taskferry` declares **zero dependencies** — required *or* optional. The bridge
code lives at `taskferry.core.otel` and imports `opentelemetry` lazily, so the
core never carries the dependency. This distribution is what pulls in
`opentelemetry-api`, so tracing is opt-in by an install, and everyone who does
not need it still installs nothing extra.

## License

Apache-2.0.
