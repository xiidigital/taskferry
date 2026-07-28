# Changelog

All notable changes to `taskport-jobs` are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-07-26

### Changed

- **BREAKING**: `taskport.jobs` is now `taskport_jobs`.
- **BREAKING**: the `JobRunner` port became `taskport.ports.JobBackend`.
  `JobHandle` → `ExecutionHandle`, `JobResult` → `ExecutionResult`, `JobStatus` →
  `ExecutionState`, `JobResources` → `Resources`, `JobCapability` → `Capability`.
- Backends are selected by routing rather than by indexing a global `runners`
  registry.
- Resource translation is now explicit and tested per provider: Kubernetes
  quantities in, vCPU/MiB for Batch and fractional cores/GiB for Azure out.

### Added

- Timeouts are reported as `TIMED_OUT` rather than collapsed into `FAILED`.
- Array-job partial completion is reported as `RUNNING`, not `SUCCEEDED`.
- Every backend runs `taskport.contract.JobBackendContract`.

### Removed

- **BREAKING**: `LocalJobRunner` — superseded by
  `taskport.backends.subprocess.SubprocessJobBackend`, built into `taskport`.
- **BREAKING**: `CloudRunJobsRunner` — moved to the `taskport-cloudrun`
  distribution.
