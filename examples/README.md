# Taskport examples

Runnable, self-contained samples. From the repo root (with the workspace synced,
`uv sync --all-packages`):

```bash
uv run python examples/jobs-local/run.py
uv run python examples/events-inmemory/run.py
uv run python examples/scheduler-local/run.py
uv run python examples/django-minimal/run.py
uv run python examples/composed-flow/run.py     # upload → Task → Event → Job
```

| Example            | Shows                                                             |
| ------------------ | ---------------------------------------------------------------- |
| `jobs-local`       | Run a finite workload with `LocalJobRunner`, poll to completion. |
| `events-inmemory`  | Pub/sub fan-out and filtering with `InMemoryEventBus`.           |
| `scheduler-local`  | Interval scheduling with `LocalScheduler.run_pending`.           |
| `django-minimal`   | The official `django.tasks` API on a Taskport backend.          |
| `composed-flow`    | Task → Event → Job composition with one correlation id.         |

Cloud examples (`jobs-gcp`, `jobs-aws`, …) follow the same shape: install the
provider extra and `configure` the registry with the provider factory. See each
package README.
