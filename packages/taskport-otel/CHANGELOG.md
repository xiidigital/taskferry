# Changelog — taskport-otel

Follows [Semantic Versioning](https://semver.org/); versions independently of the
rest of the family.

## [0.1.0]

### Added

- `configure_opentelemetry` and `OTelTracer`, the OpenTelemetry bridge as its own
  distribution. Installing `taskport-otel` pulls in `opentelemetry-api`; calling
  `configure_opentelemetry()` makes every `taskport.*` span a real OTel span.
- This removes the last optional dependency from the `taskport` core, which now
  declares nothing beyond itself. The bridge remains importable at
  `taskport.core.otel` (implementation, lazy import); `taskport-otel` is the
  distribution that carries the dependency.
