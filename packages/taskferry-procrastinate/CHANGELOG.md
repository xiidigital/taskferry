# Changelog

All notable changes to `taskferry-procrastinate` are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.3.0] — 2026-09-19

### Added

- **Native async submit** via Procrastinate's `defer_async`. `asubmit` defers on
  an async-capable App without a worker thread, and falls back to the thread path
  on an older release without `defer_async`. No new dependency.

## [0.2.0] — 2026-07-26

### Added

- Initial release, extracted from the Django-coupled backends of `taskferry-django` 0.1
  and rebuilt against the framework-agnostic Taskferry ports.
