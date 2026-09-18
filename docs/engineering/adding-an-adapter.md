# Adding an adapter

An adapter maps one engine (Procrastinate, Cloud Run Jobs, SQS, …) onto a Taskferry
port. This is the recipe; the contract suite tells you when you are done.

## 1. A new distribution

One engine, one distribution, one top-level module (ADR-0013):
`packages/taskferry-<engine>/` shipping module `taskferry_<engine>`. Copy the shape
of an existing adapter (`taskferry-procrastinate` for a task backend,
`taskferry-cloudrun` for a job backend). Each has its own `pyproject.toml`,
`README.md`, `CHANGELOG.md`, `LICENSE`, `src/`, `tests/`, and `py.typed`.

Depend on `taskferry` (`>=0.2,<0.3`) and nothing else at runtime. Put the provider
SDK in an **extra**, never in required dependencies:

```toml
dependencies = ["taskferry>=0.2,<0.3"]
[project.optional-dependencies]
aws = ["boto3>=1.34"]
```

## 2. Implement the port

Subclass `BaseBackend`. It provides the public `submit`/`get`/`result`/`cancel`
surface, applies capability checks and tracing, and delegates to the private
methods you implement:

```python
class MyBackend(BaseBackend):
    name = "myengine"
    kind = ExecutionKind.TASK  # or JOB

    def __init__(self, *, client=None, **opts):
        self._client = client  # injectable for tests

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet({Capability.SUBMIT, Capability.STATE, ...}, provider=self.name)

    def _submit(self, spec): ...
    def _get(self, execution_id): ...
    def _result(self, execution_id, *, timeout): ...
    def _cancel(self, execution_id): ...
```

Rules that make it honest:

- **Advertise only what the engine really does.** Everything else is left off the
  set, and `BaseBackend` turns a request for it into `UnsupportedCapability`
  (ADR-0005). Never accept-and-ignore.
- **Import the SDK lazily**, inside the method that needs it, behind the injectable
  `client`. Guard the import and raise `ProviderError` naming the extra to install.
- **Keep a Taskferry id ↔ engine id map** with `taskferry.tracking.ExternalIdIndex`
  if the engine mints its own ids.
- **Extract status mapping into a pure function** so it can be unit-tested without
  the engine.

## 3. Register it for discovery

Advertise the backend through the `taskferry.backends` entry-point group so
configuration can name it without importing it eagerly:

```toml
[project.entry-points."taskferry.backends"]
myengine = "taskferry_myengine:MyBackend"
```

## 4. Prove it with the contract

```python
from taskferry.contract import TaskBackendContract


class TestMyBackend(TaskBackendContract):
    def make_backend(self):
        return MyBackend(client=FakeClient())
```

The suite is capability-driven: it verifies that what you advertise works and what
you don't advertise is rejected. Add provider-specific tests for request building
and status mapping against a fake client. No cloud account is needed.

## 5. Wire up the edges

- `CHANGELOG.md` entry, `README.md` with the capability table.
- Add the extra to any relevant docs and the multicloud matrix.
- `uv run pytest && uv run mypy packages/*/src && uv run ruff check .` green.
