# Changelog

All notable changes to `taskferry-sqs` are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.3.0] — 2026-09-19

### Added

- **Native async submit** via aiobotocore. `asubmit` sends through an async SQS
  client when one is available (injected, or built from the new `aws-async`
  extra), skipping the worker thread. Without aiobotocore it falls back to the
  thread-based async path, so behaviour is unchanged where the extra is absent.
- The `aws-async` optional dependency (`aiobotocore`).

## [0.2.0] — 2026-07-27

### Added

- Initial release. Ported from the Django-coupled backend of `taskferry-django` 0.1
  onto the framework-agnostic `TaskBackend` port, with a contract-tested
  capability set and a consumer/worker side that needs no Django.
