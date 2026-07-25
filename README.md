# Taskport

**Taskport is a family of portable execution primitives for Python and Django.**

It adapts existing infrastructure and managed services to small, stable, portable
contracts. Taskport is a **ports & adapters** layer — it does not reinvent queues,
brokers, schedulers, event buses, workflow engines, or cloud infrastructure. It
makes the ones you already have interchangeable behind honest interfaces.

Taskport models four genuinely different concepts and refuses to collapse them
into one abstraction:

| Concept    | Meaning              | Consumers     | Execution                 |
| ---------- | -------------------- | ------------: | ------------------------- |
| **Task**   | "do this operation"  | usually 1     | async work                |
| **Job**    | "run this workload"  | 1             | process / container       |
| **Event**  | "this happened"      | 0..N          | fan-out                   |
| **Schedule** | "fire something when" | N/A         | triggers another capability |

## The distributions

Users install the surface they need — never `taskport-core` directly.

| Distribution         | Import              | Purpose                                                        |
| -------------------- | ------------------- | ------------------------------------------------------------- |
| `taskport-django`    | `taskport.django`   | Portable backends for the official **Django 6 Tasks** framework |
| `taskport-jobs`      | `taskport.jobs`     | Portable execution of finite workloads (ETL, GIS, ML, batch)  |
| `taskport-events`    | `taskport.events`   | Portable pub/sub + fan-out event delivery                     |
| `taskport-scheduler` | `taskport.scheduler`| Portable "when to fire" triggering                            |
| `taskport-core`      | `taskport.core`     | Tiny shared substrate (ids, capabilities, config, errors)     |

```text
                         TASKPORT
                           core
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
       django             jobs             events
        Tasks         Compute Jobs        Event Bus
          │                 │                 │
          └─────────────────┼─────────────────┘
                            │
                        scheduler
                     "when to fire"
             Cross-cutting: configuration · capabilities
             correlation · observability · security
```

## Design commitments

- **Adapt, don't reinvent.** Adapters depend on core; core never depends on a
  provider SDK. See [ADR-0001](docs/adr/0001-taskport-family.md).
- **Honest capabilities.** Providers differ. Every provider exposes a
  `capabilities` set; unsupported operations raise `UnsupportedCapabilityError`
  rather than silently pretending. See [ADR-0005](docs/adr/0005-capability-model.md).
- **Serverless is first-class.** No component assumes a permanently-running
  worker. Push, event-driven, scale-to-zero and run-to-completion are supported.
  See [ADR-0008](docs/adr/0008-delivery-semantics.md).
- **Never exactly-once.** The family assumes at-least-once with possible
  duplicates and promotes idempotency.
- **JSON payloads only.** No pickle, no ORM instances, no arbitrary objects.
- **Lightweight imports.** `import taskport.jobs` never imports `boto3`,
  `google.cloud`, `azure`, or `kubernetes` unless you use those adapters.

## Quick taste

```python
# Django — you keep writing the standard Django API:
from django.tasks import task


@task
def resize_image(image_id: int) -> None: ...


# ...and taskport supplies the portable backend behind TASKS = {...}.
```

```python
# Jobs — plain Python, no Django required:
from taskport.jobs import JobSpec, runners

handle = runners["default"].run(JobSpec(name="nightly-etl", command=["python", "etl.py"]))
result = runners["default"].get(handle)
```

## Repository layout

```text
packages/   taskport-core · taskport-django · taskport-jobs · taskport-events · taskport-scheduler
docs/       architecture · adr · family
examples/   runnable end-to-end samples, including a composed upload→task→event→job flow
tooling/    shared release/extraction helpers
```

Each package is independently buildable, versioned (SemVer, no lockstep), and
releasable to PyPI, and is structured for clean extraction to its own repository
without redesign. See [ADR-0010](docs/adr/0010-monorepo-to-multirepo.md).

## Development

```bash
uv sync --all-packages          # create the workspace venv with every package editable
uv run pytest                   # run the full suite
uv run ruff check .             # lint
uv run mypy packages            # type-check
uv run python tooling/build_all.py   # build wheels + sdists for every package
```

## Status

This is an early, coherent foundation. Tasks and Jobs are functional with real
adapters; Events and Scheduler ship real contracts with a local provider plus a
reference cloud adapter, and a documented roadmap for the rest. See
[docs/family/roadmap.md](docs/family/roadmap.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
