# ADR-0010: Delivery semantics — never exactly-once, tools for idempotency

- Status: Accepted
- Date: 2026-07-24
- History: absorbs former ADR-0018 (no exactly-once). Payload/serialization rules
  moved to [ADR-0009](0009-serialization.md).

## Context

Distributed messaging systems duplicate, reorder and redeliver. "Exactly-once
delivery" is the single most requested and least deliverable property in the
space, and a layer that abstracts several engines is under particular pressure to
claim it — the abstraction makes it *look* like something the layer could provide
on top of whatever is underneath.

## Decision

Taskferry promises **at-least-once or at-most-once, depending on the backend**, and
**never exactly-once**. `DeliveryGuarantee` has members `AT_MOST_ONCE` and
`AT_LEAST_ONCE` and, deliberately, no `EXACTLY_ONCE`. Ordering is described
separately (`UNORDERED`, `PER_KEY`, `TOTAL`).

What Taskferry provides instead of a false guarantee:

- **`idempotency_key` on every spec.** A backend advertising `DEDUPLICATION` maps
  it onto its own real mechanism — Procrastinate's `queueing_lock`, a Cloud Tasks
  task name. A backend without that capability **rejects** a keyed spec rather
  than accepting it and silently doing nothing with it ([ADR-0005](0005-capability-model.md)).
- **Honest capability reporting**, so an application can tell which kind of
  backend it is talking to before it relies on anything.
- **Documentation that says so**, in every README, the module docstrings, and here.

Every deduplication mechanism Taskferry can reach is time-bounded and best-effort:
Cloud Tasks' name uniqueness has a retention window; a `queueing_lock` only
refuses a duplicate while the first job is still pending. These are genuinely
useful, they are not exactly-once, and the docstrings say which is which.

## Alternatives considered

**Implement exactly-once above the engines with a deduplication table.** This means
owning a database, a retention policy and a distributed transaction between the
dedup write and the engine's enqueue — a queue feature
([ADR-0002](0002-not-a-task-queue.md)) — and it would *still* only be at-least-once
with a smaller window and a much larger surface. Rejected.

**Say nothing and let users assume.** The most common industry choice, and the
reason so many systems carry a duplicate-handling bug nobody knew to look for.
Rejected.

**Refuse `idempotency_key` entirely, since it cannot guarantee anything.** Rejected:
a bounded deduplication window is genuinely valuable for the double-submitted-form
case, and users would otherwise reimplement it worse.

## Consequences

- Handlers must be idempotent. This is stated wherever a handler is written.
- `idempotency_key` on a backend without `DEDUPLICATION` raises, which surprises
  some users; the alternative is a key that silently does nothing.
- Taskferry can be honestly compared with engines that *do* make the claim, and
  those claims evaluated on their own terms rather than inherited.
- Serverless is first-class: no component assumes a permanently-running worker;
  adapters prefer a provider's native push/event mechanism over a polling loop.
