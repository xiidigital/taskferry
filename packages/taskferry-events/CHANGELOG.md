# Changelog

All notable changes to `taskferry-events` are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-07-26

### Changed

- **BREAKING**: `taskferry.events` is now `taskferry_events`.

Contracts, adapters and the registry are otherwise unchanged. Only the import
path moved, so that `taskferry` could ship a public API. See
[ADR-0013](../../docs/adr/0013-packaging-strategy.md).
