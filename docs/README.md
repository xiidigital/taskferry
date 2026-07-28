# Taskport documentation

Taskport is a **portable execution layer for Python** — it models units of work,
picks the right kind of execution, and routes them to engines that already exist.

Start with the [repository README](https://github.com/taskport/taskport#readme)
for the tour. This directory holds the detail.

## Architecture

- [The 2026 refactor](architecture/refactor-2026.md) — the current design, the
  problems it solves, and how the migration was executed.
- [Security](security.md) — function resolution, import allowlists, push endpoint
  authentication, subprocess isolation, payload contents.
- [Migration 0.1 → 0.2](migration/0.1-to-0.2.md) — every renamed symbol.

## Decisions (ADRs)

The current design:

1. [Taskport is a portable execution layer](adr/0011-portable-execution-layer.md)
2. [Taskport does not implement a task queue](adr/0012-not-a-task-queue.md)
3. [Task and Job are separate primitives](adr/0013-task-vs-job.md)
4. [Capabilities define support, not method presence](adr/0014-backend-capabilities.md)
5. [Frameworks are adapters, pointing inward](adr/0015-framework-adapters.md)
6. [Typed configuration, many sources, one model](adr/0016-configuration-architecture.md)
7. [JSON payloads and named functions; never pickle](adr/0017-serialization.md)
8. [Never promise exactly-once](adr/0018-no-exactly-once.md)
9. [Exactly one layer retries](adr/0019-retry-ownership.md)
10. [One distribution per top-level module](adr/0020-packaging-strategy.md)

Still in force from 0.1:

- [ADR-0004 · Keep the shared substrate small](adr/0004-taskport-core-scope.md)
- [ADR-0005 · Capability model](adr/0005-capability-model.md) — refined by ADR-0014
- [ADR-0006 · Environment configuration](adr/0006-environment-configuration.md) — refined by ADR-0016
- [ADR-0007 · Provider credentials](adr/0007-provider-credentials.md)
- [ADR-0008 · Delivery semantics](adr/0008-delivery-semantics.md) — refined by ADR-0017, ADR-0018
- [ADR-0009 · Observability](adr/0009-observability.md)
- [ADR-0010 · Monorepo to multi-repo](adr/0010-monorepo-to-multirepo.md)

Superseded, kept for the record:

- [ADR-0001 · Taskport as a family](adr/0001-taskport-family.md) — reframed by ADR-0011
- [ADR-0002 · Separating the concepts](adr/0002-separate-task-job-event-schedule.md) — refined by ADR-0013
- [ADR-0003 · Namespace packages](adr/0003-namespace-packages.md) — superseded by ADR-0020

## Adjacent concerns

Events and Schedules are useful, separately-distributed concerns that sit
alongside the execution layer rather than inside it.

- [Task vs Job vs Event vs Schedule](family/concepts.md)
- [Composition patterns](family/composition.md)
- [Multicloud equivalence matrix](family/multicloud-matrix.md)
- [Deployment profiles](family/deployment-profiles.md)
- [Roadmap & status](family/roadmap.md)
- [Extraction to multi-repo](family/extraction.md)
