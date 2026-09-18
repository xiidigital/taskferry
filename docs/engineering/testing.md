# Testing

```bash
uv run pytest                                   # everything
uv run pytest packages/taskferry/tests           # one distribution
uv run pytest --cov --cov-report=term-missing   # with coverage
```

pytest runs in `importlib` import mode so identically-named test files
(`test_import_light.py` in several packages) coexist without `__init__.py` tricks.

## The shape of the suite

- **Unit tests** for the pure pieces: specs, capabilities, the router, retry
  policy, serialization, correlation.
- **Contract suites** — the load-bearing idea. `taskferry.contract` ships reusable,
  capability-driven test bases (`TaskBackendContract`, `JobBackendContract`,
  `InlineContract`). A backend proves itself by subclassing one and pointing it at
  an instance; the suite asserts the behaviour the ports promise. Crucially it is
  **capability-driven**: a backend that does not advertise `CANCEL` is asserted to
  *reject* cancellation, not skipped. This is how one test body keeps every adapter
  honest.
- **Architecture tests** (`test_architecture.py`, `test_capability_coherence.py`)
  encode the invariants the tooling can't: `taskferry` has zero dependencies, the
  core never imports `django.conf.settings`, importing an adapter pulls in no SDK,
  capability sets are coherent across kinds.

## Testing an adapter without the cloud

Adapters take an **injectable client**; the real SDK is only imported lazily when
no client is supplied. Tests pass a small fake that records calls and returns
canned responses, so the whole suite runs with no cloud account and no network.
Status-mapping logic is extracted into **pure functions** (e.g.
`map_execution_status`) and unit-tested against plain objects.

```python
class TestMyBackendContract(TaskBackendContract):
    def make_backend(self):
        return MyBackend(client=FakeClient())
```

## What to test

Test **behaviour and invariants**, not coverage percentages. A new capability is a
new contract assertion; a new backend is a new contract subclass plus its
provider-specific request-building and status-mapping tests. Real cloud
integration tests are optional and never required for a normal run.
