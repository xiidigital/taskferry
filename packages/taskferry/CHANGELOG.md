# Changelog

All notable changes to `taskferry` are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## Unreleased

### Changed

- Serialize `RetryPolicy` ownership and jitter in task envelopes, with backward
  compatible defaults for existing envelopes.

### Added

- `RetryPolicy.accepts_exception()` and `filtered_out()` helpers for adapters
  that need provider-specific exception-filter handling.

## [0.2.1] — 2026-11-27

### Added

- Provider-observed `TaskExecutionContext` for portable execution identity,
  attempt number, worker id and start time where an adapter can report them.
- `ATTEMPT_COUNT` and `DELETE_PENDING` capabilities for provider operations that
  are distinct from durable state and cancellation.

### Changed

- `ExecutionHandle` exposes capability-checked `attempt_count()` and
  `delete_pending()` operations.

## [0.2.0] — 2026-07-26

This release defines the portable Taskferry execution runtime. See
[ADR-0001](../../docs/adr/0001-portable-execution-layer.md) for its design.

### Added

- **A public API.** `from taskferry import Taskferry, TaskSpec, JobSpec, Execution,
  ExecutionHandle` is available from a regular package with a curated
  `__all__`.
- **Three execution primitives** — `InlineSpec`, `TaskSpec`, `JobSpec` — modelled
  independently of any framework.
- **`Taskferry` runtime** with `inline` / `tasks` / `jobs` facades, lazy backend
  construction, a bounded execution index and context-manager lifecycle.
- **`Router`** with ordered, glob-capable rules on kind, queue, profile, name and
  labels; first match wins, and an unroutable spec names every rule it tried.
- **`Execution`, `ExecutionState`, `ExecutionHandle`** — one portable identity and
  one documented state machine shared by every backend of every kind.
- **A single `Capability` enum** shared across execution kinds.
- **`RetryPolicy` / `TimeoutPolicy`** with explicit `RetryOwner`, so exactly one
  layer retries. See [ADR-0011](../../docs/adr/0011-retry-ownership.md).
- **`FunctionRef` / `FunctionRegistry`** — the portable
  `package.module:function` reference, with an import allowlist for untrusted
  queues.
- **Four built-in backends**: `InlineExecutionBackend`, `ThreadTaskBackend`,
  `ProcessTaskBackend`, `SubprocessJobBackend`.
- **`TaskferryConfig`** — typed, validated at construction, loadable from a
  mapping, from `TASKFERRY_*` environment variables, or built in Python.
- **Plugin discovery** through the `taskferry.backends` entry-point group.
- **`taskferry.contract`** — reusable, capability-driven contract suites every
  adapter runs.
- **Hooks** (`before_submit`, `after_submit`, `on_success`, `on_failure`,
  `on_retry`, ...) as composable objects, with a documented ordering guarantee.
- **`ExternalIdIndex`** — the bounded Taskferry-id ↔ engine-id map every adapter
  needs.
- **A CLI**: `taskferry backends | capabilities | route | submit-task | submit-job
  | status | cancel | result | doctor`.
- **Architectural tests** enforcing zero dependencies three independent ways.

### Changed

- The `taskferry` distribution includes the `taskferry.core` module.
- `QUEUED → SUCCEEDED` is a legal transition: a worker that finishes before the
  first poll is normal, not a broken adapter.

### Fixed

- `UnsupportedCapability` is now an alias of `UnsupportedCapabilityError` rather
  than a subclass. As a subclass, the documented `except UnsupportedCapability`
  missed everything raised by `CapabilitySet.require()`.
