# Changelog

All notable changes to `taskferry-django` are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## Unreleased

## [0.3.0] — 2026-11-26

### Changed

- **BREAKING**: Require Django 6.0+, matching the native `django.tasks` imports.
  The package does not target the third-party backport on Django 5.x.
- Do not advertise `supports_async_task` or `supports_get_result`: the dispatcher
  is synchronous and the bridge does not persist Django-specific `TaskResult`
  records. Calling `get_result()` raises `NotImplementedError`.

## [0.2.0] — 2026-07-26

### Changed

- Reduced to a pure bridge. `TaskferryBackend` is a `django.tasks` backend that
  translates to `TaskSpec` and hands it to a Taskferry runtime; the engine is a
  configuration choice, not a Django class.
- Feature flags and capabilities now come from the *routed* backend rather than
  being hardcoded per class.

### Added

- `config_from_settings()` — the only place in the project that reads
  `django.conf.settings`.
- `submit_on_commit()` / `task_on_commit()`, built on `transaction.on_commit`.
- System checks that build every configured backend at `manage.py check` time.
- `manage.py taskferry <subcommand>`, wrapping the same CLI.
- `taskferry_django.execute:run_task`, the single worker-side dispatcher.

### Removed

- Provider integrations are maintained by their dedicated distributions,
  including `taskferry-cloudtasks`, `taskferry-procrastinate`, `taskferry-sqs`,
  `taskferry-servicebus`, and `taskferry-dramatiq`.
- The Cloud Tasks request handler is available as
  `taskferry_cloudtasks.handle_request` and accepts request bytes.
- Backend support is described by `taskferry.Capability`.
