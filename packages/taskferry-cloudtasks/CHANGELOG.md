# Changelog

All notable changes to `taskferry-cloudtasks` are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- `ATTEMPT_COUNT` through `get_task()` while Cloud Tasks retains the task, and
  `DELETE_PENDING` through `delete_task()` for scheduled or dispatched tasks.
- Current attempt propagation from Cloud Tasks retry headers into execution
  context.

### Changed

- Keep `STATE`, `RESULT`, and `CANCEL` unadvertised; attempt inspection and
  pending-task deletion do not provide those semantics.

## [0.3.0] — 2026-09-19

### Added

- **Native async submit** via `CloudTasksAsyncClient`. `asubmit` creates the task
  through the async client (injected, or built from the existing `gcp` extra),
  skipping the worker thread, and falls back to the thread path when the SDK is
  absent. No new dependency — the async client ships with `google-cloud-tasks`.

## [0.2.0] — 2026-07-26

### Added

- Initial release of the Google Cloud Tasks backend, implementing Taskferry's
  framework-independent `TaskBackend` contract.
