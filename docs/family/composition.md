# Composition patterns

The four concepts compose. Composition is done in **application code**, by
capability, not by Taskport merging the abstractions or auto-wiring them (section
45). Taskport is not, and will not become, a workflow/DAG engine (section 46).

## Supported compositions

```text
Task   → Job        a Task submits a heavy Job and returns
Event  → Task        an event consumer enqueues a Task
Event  → Job         an event consumer submits a Job
Schedule → Task      a schedule fires a Task
Schedule → Job       a schedule fires a Job
Schedule → Event     a schedule publishes an Event
```

## Example: Task → Job

Keep the packages decoupled — the Task just calls a `JobRunner`:

```python
from django.tasks import task
from taskport.jobs import JobSpec, runners


@task
def start_raster_pipeline(dataset_id: str) -> str:
    handle = runners["heavy"].run(
        JobSpec(name="raster", command=["python", "-m", "pipeline", dataset_id])
    )
    return handle.id
```

## Example: Event → Task and Event → Job

```python
from taskport.events import Event, subscribers
from django.tasks import task
from taskport.jobs import JobSpec, runners


@task
def send_welcome(user_id: str) -> None: ...


def on_user_registered(event: Event) -> None:
    send_welcome.enqueue(event.data["user_id"])


def on_dataset_updated(event: Event) -> None:
    runners["default"].run(JobSpec(name="reindex", command=["python", "reindex.py"]))
```

## Correlation ties it together

Every step propagates a `Correlation` (same `correlation_id`, advancing
`causation_id`) so an `HTTP request → Task → Event → Job` chain is observable as
one logical flow (section 19) — **without** any orchestration engine. The
end-to-end example lives in [`examples/composed-flow`](../../examples/composed-flow).

## Explicitly out of scope

DAG workflows, state machines, and replacements for Temporal / Airflow / Step
Functions / Cloud Workflows are **not** part of Taskport. Taskport may integrate
with them later; it will not reinvent them.
