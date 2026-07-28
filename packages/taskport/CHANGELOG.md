# Changelog

All notable changes to `taskport` are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-07-26

The distribution formerly published as `taskport-core`, reframed as the portable
execution layer itself. See [ADR-0011](../../docs/adr/0011-portable-execution-layer.md)
and the [migration guide](../../docs/migration/0.1-to-0.2.md).

### Added

- **A public API.** `from taskport import Taskport, TaskSpec, JobSpec, Execution,
  ExecutionHandle` now works; `taskport` is a regular package with a curated
  `__all__`.
- **Three execution primitives** — `InlineSpec`, `TaskSpec`, `JobSpec` — modelled
  framework-agnostically for the first time. Tasks previously existed only inside
  Django.
- **`Taskport` runtime** with `inline` / `tasks` / `jobs` facades, lazy backend
  construction, a bounded execution index and context-manager lifecycle.
- **`Router`** with ordered, glob-capable rules on kind, queue, profile, name and
  labels; first match wins, and an unroutable spec names every rule it tried.
- **`Execution`, `ExecutionState`, `ExecutionHandle`** — one portable identity and
  one documented state machine shared by every backend of every kind.
- **A single `Capability` enum** shared across kinds, replacing the per-domain
  enums that could not be compared.
- **`RetryPolicy` / `TimeoutPolicy`** with explicit `RetryOwner`, so exactly one
  layer retries. See [ADR-0019](../../docs/adr/0019-retry-ownership.md).
- **`FunctionRef` / `FunctionRegistry`** — the portable
  `package.module:function` reference, with an import allowlist for untrusted
  queues.
- **Four built-in backends**: `InlineExecutionBackend`, `ThreadTaskBackend`,
  `ProcessTaskBackend`, `SubprocessJobBackend`.
- **`TaskportConfig`** — typed, validated at construction, loadable from a
  mapping, from `TASKPORT_*` environment variables, or built in Python.
- **Plugin discovery** through the `taskport.backends` entry-point group.
- **`taskport.contract`** — reusable, capability-driven contract suites every
  adapter runs.
- **Hooks** (`before_submit`, `after_submit`, `on_success`, `on_failure`,
  `on_retry`, ...) as composable objects, with a documented ordering guarantee.
- **`ExternalIdIndex`** — the bounded Taskport-id ↔ engine-id map every adapter
  needs.
- **A CLI**: `taskport backends | capabilities | route | submit-task | submit-job
  | status | cancel | result | doctor`.
- **Architectural tests** enforcing zero dependencies three independent ways.

### Changed

- `taskport-core` is absorbed into `taskport`. `taskport.core` remains a valid
  import path.
- `QUEUED → SUCCEEDED` is a legal transition: a worker that finishes before the
  first poll is normal, not a broken adapter.

### Fixed

- `UnsupportedCapability` is now an alias of `UnsupportedCapabilityError` rather
  than a subclass. As a subclass, the documented `except UnsupportedCapability`
  missed everything raised by `CapabilitySet.require()`.

### Removed

- `taskport-core` as a separate distribution. Depend on `taskport`.
