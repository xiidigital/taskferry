# Changelog

All notable changes to `taskferry-servicebus` are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.3.0] — 2026-09-19

### Added

- **Native async submit** via `azure.servicebus.aio`. `asubmit` sends through the
  async client, skipping the worker thread, and falls back to the thread path
  when the SDK is absent. No new dependency — the async client ships with
  `azure-servicebus`.

## [0.2.0] — 2026-07-27

### Added

- Initial release of the Azure Service Bus task backend, implementing Taskferry's
  framework-independent `TaskBackend` contract with consumer and worker support.
