# Changelog — taskferry-otel

Follows [Semantic Versioning](https://semver.org/); versions independently of the
rest of the family.

## [0.1.0]

### Added

- `configure_opentelemetry` and `OTelTracer`, the OpenTelemetry bridge as its own
  distribution. Installing `taskferry-otel` pulls in `opentelemetry-api`; calling
  `configure_opentelemetry()` makes every `taskferry.*` span a real OTel span.
- This removes the last optional dependency from the `taskferry` core, which now
  declares nothing beyond itself. The bridge remains importable at
  `taskferry.core.otel` (implementation, lazy import); `taskferry-otel` is the
  distribution that carries the dependency.
