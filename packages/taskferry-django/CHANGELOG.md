# Changelog

All notable changes to `taskferry-django` are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-07-26

### Changed

- **BREAKING**: `taskferry.django` is now `taskferry_django`.
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

- **BREAKING**: the Django-coupled provider backends. Cloud Tasks and
  Procrastinate moved to their own framework-agnostic distributions; SQS, Azure
  Service Bus and Dramatiq were removed pending a proper port. See the
  [migration guide](../../docs/migration/0.1-to-0.2.md).
- `taskferry.django.views.task_webhook` — replaced by
  `taskferry_cloudtasks.handle_request`, which takes bytes and works in any
  framework.
- `TaskCapability` — use `taskferry.Capability`.
