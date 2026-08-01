# Taskport examples

Runnable, self-contained. From the repo root with the workspace synced
(`uv sync --all-packages`):

```bash
uv run python examples/quickstart/run.py          # the sixty-second tour
uv run python examples/routing/run.py             # one call site, several engines
uv run python examples/jobs-local/run.py          # jobs: exit codes, timeouts, cancel
uv run python examples/composed-flow/run.py       # task -> job, one correlation id
uv run python examples/django-minimal/run.py      # django.tasks on a Taskport backend
uv run python examples/events-inmemory/run.py     # pub/sub fan-out
uv run python examples/scheduler-local/run.py     # interval scheduling
```

| Example | Shows | Needs |
| --- | --- | --- |
| `quickstart` | inline, tasks and jobs from one runtime | nothing |
| `routing` | changing engines without touching call sites | nothing |
| `jobs-local` | the full Job lifecycle against a subprocess | nothing |
| `composed-flow` | a task launching a job, correlation intact | nothing |
| `django-minimal` | the standard `django.tasks` API on a Taskport backend | Django |
| `events-inmemory` | fan-out, filtering, replay | nothing |
| `scheduler-local` | `LocalScheduler.run_pending` | nothing |

The first four run with **no infrastructure at all** — no database, no broker, no
cloud account, no worker process. That is the point.
