# Changelog

All notable changes to `taskferry-cloudrun` are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.3.0] — 2026-09-19

### Added

- **Live log streaming** through Cloud Logging. `stream_logs` pages an execution's
  log entries in timestamp order, de-duplicating by insert id, and follows them
  until the execution reaches a terminal state; `logs` returns the whole output
  once. `logs_uri` (the console link) is unchanged.
- `CloudRunJobBackend` accepts an injected `logging_client`.

## [0.2.0] — 2026-07-26

### Added

- Initial release, extracted from the Django-coupled backends of `taskferry-django` 0.1
  and rebuilt against the framework-agnostic Taskferry ports.
