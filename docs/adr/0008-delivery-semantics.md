# ADR-0008: At-least-once, never exactly-once; JSON-only payloads

- Status: Accepted
- Date: 2026-07-24

## Context

Distributed messaging systems can duplicate, reorder, and redeliver. Promising
"exactly-once" is a lie that leads to data corruption when users trust it.
Likewise, unsafe serialization (pickle) and serializing live objects/ORM rows
cause security holes and portability failures.

## Decision

**Delivery:**

- Taskport assumes **at-least-once with possible duplicates** as the baseline.
- Adapters declare their real semantics via `DeliveryGuarantee`
  (`AT_MOST_ONCE`, `AT_LEAST_ONCE`) and `Ordering`
  (`UNORDERED`, `PER_KEY`, `TOTAL`). `EXACTLY_ONCE` does not exist in the enum.
- Idempotency is **promoted** (helpers where they add value, e.g. stable keys),
  never imposed as a framework. Every domain doc restates: expect duplicates.

**Serialization:**

- **JSON only** by default. `JsonSerializer` rejects non-serializable input
  eagerly.
- **No pickle** for transport/untrusted messages.
- **No ORM instances**, no arbitrary Python objects. Pass identifiers:
  `process_resource(resource_id)`, not `process_resource(resource)`.

**Serverless is first-class (section 44):** no component assumes a permanently
running worker. Push, event-driven, scale-to-zero and run-to-completion models
are all supported; adapters prefer the provider's native push/event mechanism
over a long-lived polling loop when one exists.

## Consequences

- Users design idempotent handlers; Taskport gives them the vocabulary and hooks
  to do so.
- Payloads are inspectable, language-agnostic, and safe to move across trust
  boundaries.
- Some convenience (passing rich objects) is deliberately unavailable.
