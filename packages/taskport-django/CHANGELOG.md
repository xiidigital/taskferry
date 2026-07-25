# Changelog — taskport-django

Follows [Semantic Versioning](https://semver.org/); versions independently of the
rest of the family (ADR-0010).

## [Unreleased]

## [0.1.0]

### Added

- `TaskportTaskBackend` base + `TaskportCapabilityMixin` (honest capability set
  derived from Django's backend feature flags plus provider extras).
- `TaskCapability` capability vocabulary mapped onto Django 6's `supports_*`
  flags.
- Backends: `LocalBackend` (dev, over Django's `ImmediateBackend`),
  `CloudTasksBackend` (GCP serverless push), `SQSBackend` (AWS).
- Portable JSON task message format (`build_message`, `resolve_task`,
  `TaskMessage`) — identifiers/argv only, no pickle or ORM instances.
- Consumer side: `execute_task_message`, `task_webhook` view (Cloud Tasks push),
  `process_sqs_message` / `process_sqs_event` (SQS pull).
- Correlation propagation from enqueue through execution.
- `TaskBackendContract` reusable, capability-driven contract test suite.
- Uses the official `TASKS` setting (no `TASKPORT_TASKS` duplication).
- `py.typed`.
