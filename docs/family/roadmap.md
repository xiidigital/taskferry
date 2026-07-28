# Roadmap & status

Versioning is per-distribution SemVer with **no lockstep**. Every distribution is
independently buildable and releasable.

## What is done

The vertical slice the design targets is implemented and tested:

```mermaid
flowchart TD
    APP["Python application"]
    TP["Taskport"]

    APP --> TP

    TP --> LOCAL["Local<br/>inline · thread · process · subprocess"]
    TP --> PRO["Procrastinate<br/>background tasks"]
    TP --> CR["Cloud Run Jobs<br/>isolated workloads"]
```

That covers immediate execution, background task execution and external heavy job
execution behind one API — which was the condition for calling the layer usable.

### `taskport` — 0.2.0

Runtime, router, capabilities, executions and handles, specs, retry and timeout
policies, function registry, hooks, plugin discovery, configuration, CLI,
reusable contract suites, and four built-in local backends. Zero dependencies,
enforced three ways. 87% covered.

### Adapters — 0.2.0

| Distribution | Status |
| --- | --- |
| `taskport-procrastinate` | ✅ task backend + worker dispatcher, contract-tested |
| `taskport-cloudtasks` | ✅ task backend + framework-agnostic receiver |
| `taskport-cloudrun` | ✅ job backend, contract-tested |
| `taskport-jobs` | ✅ AWS Batch, Kubernetes, Azure Container Apps job backends |
| `taskport-django` | ✅ `django.tasks` backend, settings, on-commit, checks, CLI |
| `taskport-events` | ✅ in-memory, Pub/Sub, SNS/EventBridge, Event Grid, Kafka |
| `taskport-scheduler` | ✅ local, Cloud Scheduler, EventBridge, CronJob |

## What is next

Ordered by how much each would teach us about whether the abstraction holds.

1. **A Celery task backend.** Celery's model differs from both Procrastinate's and
   Cloud Tasks' — it has its own result backend, its own routing, its own
   serializers. If `TaskSpec` survives Celery unchanged, the task port is right.
2. **Restore the 0.1 pull-based backends** — SQS, Azure Service Bus, Dramatiq —
   against the framework-agnostic `TaskBackend` port. Removed rather than
   half-ported in 0.2; see the [migration guide](../migration/0.1-to-0.2.md).
3. **Log streaming for jobs.** Every job backend exposes a log *location*; none
   streams. It is a real gap for anyone watching a long GDAL run.
4. **An async API.** The current surface is synchronous, and `async def` callables
   are executed correctly, but `await runtime.tasks.submit(...)` does not exist.
   Adding it needs a decision about whether adapters implement both or one is
   derived — worth doing deliberately rather than by accident.
5. **`taskport-otel`.** The tracer bridge lives in `taskport.core.otel` behind an
   extra. Moving it to its own distribution would remove the last optional
   dependency from the core.

## What will never be built

Anything belonging to an execution engine rather than to a portability layer: a
PostgreSQL or Redis queue, worker daemons, task reservation, heartbeats, worker
registries, distributed locks, broker protocols, queue polling, a scheduler
daemon, or a DAG/saga/durable-workflow runtime.

This is not a "not yet" list. See [ADR-0012](../adr/0012-not-a-task-queue.md).
