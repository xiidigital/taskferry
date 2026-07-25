# ADR-0005: Honest capability model, no lying abstractions

- Status: Accepted
- Date: 2026-07-24

## Context

Providers do not support the same features. Cloud Tasks supports scheduled
delivery and per-task delay; a naive Redis worker may not. AWS Batch supports GPU
and CPU/memory overrides; a local subprocess does not. A portable API that
silently ignores unsupported options — or pretends to honor them — is dangerous.

## Decision

Every domain defines a capability enum (subclass of `taskport.core.Capability`,
a `StrEnum`). Every provider exposes an immutable
`taskport.core.CapabilitySet` via a `capabilities` property.

Callers choose their style:

- **Feature detection:** `if TaskCapability.DELAY in backend.capabilities: ...`
- **Assertion:** `backend.capabilities.require(TaskCapability.DELAY)` raises
  `UnsupportedCapabilityError` (a `TaskportError`) naming the capability and
  provider.

When an operation requires a capability the provider lacks, the adapter **must**
either fail loudly with `UnsupportedCapabilityError` or (where a caller opted in
via feature detection) degrade explicitly and visibly. It must **never** silently
drop or fake the option.

Capability vocabularies (initial):

- **Task:** priority, delay, scheduled_execution, result_tracking, cancellation,
  queue_selection, retries, dead_letter, ordering, async_enqueue. These map
  directly onto Django 6's backend feature flags (`supports_defer`,
  `supports_async_task`, `supports_get_result`, `supports_priority`).
- **Job:** status, cancel, logs, timeout, parallelism, cpu_override,
  memory_override, gpu, environment_override.
- **Event:** fanout, ordering, delivery_retry, dead_letter, filtering, replay,
  retention.
- **Schedule:** one_shot, cron, timezone, pause, resume, update, delete.

## Consequences

- Portable code can be written defensively and predictably.
- Adapters are self-describing; contract tests (section 37) use the capability
  set to decide which assertions are mandatory for a given provider.
- The vocabulary is additive: new capabilities are new enum members, not
  breaking changes.
