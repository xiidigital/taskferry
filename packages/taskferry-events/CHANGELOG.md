# Changelog

All notable changes to `taskferry-events` are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.3.0] — 2026-09-19

### Added

- **Async publish surface.** `EventPublisher` gains `apublish`/`apublish_batch`,
  and `BaseEventPublisher` derives them from the sync path with
  `asyncio.to_thread` — so every adapter has a correct, non-blocking async path
  for free (mirrors ADR-0015 for the task/job backends). An adapter overrides
  `_apublish` to skip the thread; correctness never depends on it doing so.
- **Native async** for the AWS adapters (`SnsPublisher`, `EventBridgePublisher`)
  via aiobotocore (new `aws-async` extra), and a thread-free `_apublish` for the
  in-process `InMemoryEventBus`. Pub/Sub, Event Grid and Kafka use the correct
  `to_thread` default for now (Kafka's client has no asyncio API).

## [0.2.0] — 2026-07-26

### Changed

- **BREAKING**: `taskferry.events` is now `taskferry_events`.

Contracts, adapters and the registry are otherwise unchanged. Only the import
path moved, so that `taskferry` could ship a public API. See
[ADR-0013](../../docs/adr/0013-packaging-strategy.md).
