# ADR-0004: `taskferry-core` stays intentionally tiny

- Status: Accepted
- Date: 2026-07-24

## Context

A shared package tends to accumulate everything "just in case", becoming a
god-dependency that every other package (and their transitive users) must carry.

## Decision

`taskferry-core` contains **only genuinely transversal** concerns and has **zero
runtime dependencies**. It never imports Django, a cloud SDK, Redis, a broker, or
Kubernetes.

In scope:

- Identifiers (`TaskferryId`, `new_id`)
- Correlation metadata + trace-context propagation
- Provider metadata (the provider-specific escape hatch)
- Capability model (`Capability`, `CapabilitySet`)
- Configuration primitives (`env_*`, `ProviderOptions`, `resolve_factory`)
- Serialization (`JsonSerializer`, `ensure_json_serializable`)
- Observability hooks (`Tracer`/`Span` protocols, `NoopTracer`, span/attr names)
- `LazyRegistry`
- Delivery vocabulary (`DeliveryGuarantee`, `Ordering`)
- Root error hierarchy

Out of scope (lives in domain packages): `Task`/`Job`/`Event`/`Schedule` types,
runners, publishers, schedulers, backends, any provider code.

A type is promoted to core only when **two or more** domains genuinely share it,
not for centralization's sake (section 48).

## Consequences

- Domain packages depend on core; core depends on nothing.
- Core can reach high coverage (>=90%) and stay stable.
- Some duplication across domains is accepted over premature shared abstraction
  (YAGNI, section 59).
