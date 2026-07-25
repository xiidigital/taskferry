# Changelog — taskport-jobs

Follows [Semantic Versioning](https://semver.org/); versions independently of the
rest of the family (ADR-0010).

## [Unreleased]

## [0.1.0]

### Added

- Domain model: `JobSpec`, `JobResources`, `JobHandle`, `JobResult`, `JobStatus`.
- `JobRunner` port + `BaseJobRunner` (capability enforcement + tracing).
- `JobCapability` capability vocabulary.
- `LocalJobRunner` — real subprocess execution (status, cancel, timeout, logs).
- Cloud adapters with lazy SDK imports and injectable clients:
  `CloudRunJobsRunner` (GCP), `BatchRunner` (AWS), `ContainerAppsJobsRunner`
  (Azure), `KubernetesJobRunner`.
- Pure status-mapping functions per provider.
- `runners` lazy registry (local registered as `"default"`).
- `JobRunnerContract` reusable, capability-driven contract test suite.
- Import-lightness guarantee: importing the package pulls in no cloud SDK.
- `py.typed`.
