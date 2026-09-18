# Taskferry documentation

Taskferry is a **portable execution layer for Python** — it models units of work,
picks the right kind of execution, and routes them to engines that already exist.

Start with the [repository README](https://github.com/taskferry/taskferry#readme)
for the tour. This directory holds the detail.

## Architecture

- [The 2026 refactor](architecture/refactor-2026.md) — the current design, the
  problems it solves, and how the migration was executed.
- [Async, threads and processes](concurrency.md) — what happens to an `async def`,
  which objects are shareable, and what crosses a process boundary.
- [Security](security.md) — function resolution, import allowlists, push endpoint
  authentication, subprocess isolation, payload contents.
- [Migration 0.1 → 0.2](migration/0.1-to-0.2.md) — every renamed symbol.

## Engineering (how it is built)

- [Coding standards](engineering/coding-standards.md) — what tooling enforces and
  the conventions it can't.
- [Testing](engineering/testing.md) — the contract suites, architecture tests, and
  testing adapters without the cloud.
- [Adding an adapter](engineering/adding-an-adapter.md) — the recipe for a new
  backend, end to end.
- [Release process](engineering/release.md) — per-distribution SemVer and Trusted
  Publishing.
- [CI](engineering/ci.md) — the four gates and how they map to the invariants.

See also [`CONTRIBUTING.md`](https://github.com/taskferry/taskferry/blob/main/CONTRIBUTING.md)
at the repository root for setup.

## Decisions (ADRs)

One consolidated, in-order sequence. Superseded 0.1 decisions were folded into the
ADR that replaced them; the old numbers map forward in
[the ADR index](adr/README.md).

**Foundations**

1. [Taskferry is a portable execution layer](adr/0001-portable-execution-layer.md)
2. [Taskferry does not implement a task queue](adr/0002-not-a-task-queue.md)
3. [Task and Job are separate primitives](adr/0003-task-and-job.md)
4. [Keep the shared core small](adr/0004-core-scope.md)

**The model**

5. [Capabilities define support, not method presence](adr/0005-capability-model.md)
6. [Frameworks are adapters, pointing inward](adr/0006-framework-adapters.md)
7. [Typed configuration, many sources, one model](adr/0007-configuration.md)
8. [Provider-native credential chains](adr/0008-provider-credentials.md)

**Semantics**

9. [JSON payloads and named functions; never pickle](adr/0009-serialization.md)
10. [Delivery semantics — never exactly-once](adr/0010-delivery-semantics.md)
11. [Exactly one layer retries](adr/0011-retry-ownership.md)
12. [Observability is designed in, optional to depend on](adr/0012-observability.md)

**Packaging & delivery**

13. [One distribution per top-level module](adr/0013-packaging-strategy.md)
14. [Monorepo now, clean multi-repo extraction later](adr/0014-monorepo-to-multirepo.md)
15. [A second runtime for async](adr/0015-async-api.md)

## Adjacent concerns

Events and Schedules are useful, separately-distributed concerns that sit
alongside the execution layer rather than inside it.

- [Task vs Job vs Event vs Schedule](family/concepts.md)
- [Composition patterns](family/composition.md)
- [Multicloud equivalence matrix](family/multicloud-matrix.md)
- [Deployment profiles](family/deployment-profiles.md)
- [Roadmap & status](family/roadmap.md)
- [Extraction to multi-repo](family/extraction.md)
