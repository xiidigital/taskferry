# Changelog — taskport-core

All notable changes to `taskport-core` are documented here. The project follows
[Semantic Versioning](https://semver.org/); each Taskport package versions
independently (no lockstep — see ADR-0010 / section 28).

## [Unreleased]

## [0.1.0]

### Added

- Initial release of the shared substrate.
- Identifiers: `TaskportId`, `new_id`, `is_taskport_id`.
- Correlation: `Correlation`, `use_correlation`, `current_correlation`,
  `ensure_correlation`, W3C trace-context propagation via headers.
- `ProviderMetadata` provider-specific escape hatch.
- Capability model: `Capability`, `CapabilitySet`, `UnsupportedCapabilityError`.
- Configuration: `env_str/env_int/env_bool`, `ProviderOptions`, `resolve_factory`,
  `require`.
- Serialization: `JsonSerializer`, `Serializer`, `ensure_json_serializable`.
- Observability: `Tracer`/`Span` protocols, `NoopTracer`, `span`, semantic span
  and attribute names; optional `otel` extra.
- `LazyRegistry` for import-light provider construction.
- Delivery vocabulary: `DeliveryGuarantee`, `Ordering`.
- Root error hierarchy.
- `py.typed` (PEP 561).
