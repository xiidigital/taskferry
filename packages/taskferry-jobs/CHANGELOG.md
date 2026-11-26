# Changelog

All notable changes to `taskferry-jobs` are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.3.0] — 2026-09-19

### Added

- **Live log streaming** for the Kubernetes and AWS Batch backends. `stream_logs`
  follows a job's output line by line until it terminates (Kubernetes reads the
  pod log through `CoreV1Api`; Batch tails its CloudWatch log stream), and `logs`
  returns the whole output once. Both wait for the log source to exist before
  reading, so a just-submitted job does not raise.
- `KubernetesJobBackend` accepts an injected `core_api`; `BatchJobBackend` accepts
  an injected `logs_client` and a configurable `log_group`.

## [0.2.0] — 2026-07-26

### Changed

- Resource translation is explicit and tested per provider: Kubernetes
  quantities in, vCPU/MiB for Batch and fractional cores/GiB for Azure out.

### Added

- AWS Batch, Azure Container Apps, and Kubernetes job backends implementing the
  shared `JobBackend` contract.
- Timeouts are reported as `TIMED_OUT` rather than collapsed into `FAILED`.
- Array-job partial completion is reported as `RUNNING`, not `SUCCEEDED`.
- Every backend runs `taskferry.contract.JobBackendContract`.
