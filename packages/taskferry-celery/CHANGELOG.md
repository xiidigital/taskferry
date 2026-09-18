# Changelog — taskferry-celery

Follows [Semantic Versioning](https://semver.org/); versions independently of the
rest of the family.

## [0.1.0]

### Added

- `CeleryTaskBackend` — sends Taskferry tasks to a Celery app via `send_task`,
  translating `TaskSpec` (queue, priority, delay) out and Celery task state back.
- Honest capabilities: submit, state, cancel, delay, priority, retry.
  `result` and `deduplication` are deliberately not advertised.
- Engine-owned retries via the dispatcher's `self.retry`.
- `register_dispatcher` + `execute_message` worker side, with an optional
  `FunctionRegistry` import allowlist.
- Pure `map_state` status mapping and the shared JSON envelope.
- `taskferry.backends` entry point (`celery`). Contract-tested with a fake app;
  neither Celery nor a broker is required to run the tests.
