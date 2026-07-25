# Changelog — taskport-scheduler

Follows [Semantic Versioning](https://semver.org/); versions independently of the
rest of the family (ADR-0010).

## [Unreleased]

## [0.1.0]

### Added

- Triggers: `CronTrigger`, `IntervalTrigger`, `OneShotTrigger` (each maps to a
  required capability).
- Targets: `HttpTarget`, `PubSubTarget`, `CallableTarget`.
- `Schedule`, `ScheduleHandle`, `ScheduleStatus`, `FireRecord` models.
- `Scheduler` port + `BaseScheduler` (capability enforcement + tracing).
- `ScheduleCapability` capability vocabulary.
- `LocalScheduler` — real in-process interval/one-shot scheduler with
  deterministic `run_pending` and an optional background thread.
- `CloudSchedulerScheduler` (GCP) reference adapter with lazy SDK import and
  injectable client.
- `schedulers` lazy registry (local registered as `"default"`).
- `SchedulerContract` reusable contract test suite.
- Import-lightness guarantee and `py.typed`.
