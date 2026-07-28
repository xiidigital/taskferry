# ADR-0018: Never promise exactly-once; provide tools for idempotency

- Status: Accepted
- Date: 2026-07-26
- Refines: [ADR-0008](0008-delivery-semantics.md)

## Context

"Exactly-once delivery" is the single most requested and least deliverable
property in distributed messaging. A layer that abstracts several engines is under
particular pressure to claim it, because the abstraction makes it *look* like
something the layer could provide on top of whatever is underneath.

## Decision

Taskport promises **at-least-once or at-most-once, depending on the backend**, and
never exactly-once. `DeliveryGuarantee` has no `EXACTLY_ONCE` member, on purpose.

What Taskport provides instead:

- **`idempotency_key`** on every spec. A backend advertising `DEDUPLICATION` maps
  it onto its own real mechanism — Procrastinate's `queueing_lock`, a Cloud Tasks
  task name. A backend without that capability **rejects** a keyed spec rather
  than accepting it and doing nothing with it.
- **Honest capability reporting**, so an application can tell which kind of
  backend it is talking to before it relies on anything.
- **Documentation that says so**, in the README, in the module docstrings, and
  here.

Every deduplication mechanism Taskport can reach is time-bounded and best-effort.
Cloud Tasks' name uniqueness has a retention window; a `queueing_lock` only
refuses a duplicate while the first job is still pending. These are genuinely
useful. They are not exactly-once, and the docstrings say which is which.

## Alternatives considered

**Implement exactly-once above the engines with a deduplication table.** This
means owning a database, a retention policy, and a distributed transaction between
the dedup write and the engine's enqueue. It is a queue feature
([ADR-0012](0012-not-a-task-queue.md)), and it would still not be exactly-once —
only at-least-once with a smaller window and a much larger surface. Rejected.

**Say nothing and let users assume.** The most common industry choice, and the
reason so many systems have a duplicate-handling bug nobody knew to look for.
Rejected.

**Refuse `idempotency_key` entirely, since it cannot guarantee anything.** Also
rejected: a bounded deduplication window is genuinely valuable for the
double-submitted-form case, and users would otherwise reimplement it worse.

## Consequences

- Handlers must be idempotent. This is stated wherever a handler is written.
- `idempotency_key` on a backend without `DEDUPLICATION` raises. Some users will
  find that surprising; the alternative is a key that silently does nothing.
- Taskport can be honestly compared with engines that do make the claim — and
  those claims can then be evaluated on their own terms rather than inherited.
