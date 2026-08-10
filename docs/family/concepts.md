# Task vs Job vs Event vs Schedule

Taskport keeps four concepts distinct on purpose (ADR-0003). Choosing the right
one is the most important design decision when using the family.

| Concept    | Meaning                | Consumers     | Execution                     | Package               |
| ---------- | ---------------------- | ------------- | ----------------------------- | --------------------- |
| **Task**   | "do this operation"    | usually 1     | async work                    | `taskport-django`     |
| **Job**    | "run this workload"    | 1             | process / container           | `taskport-jobs`       |
| **Event**  | "this happened"        | 0..N          | fan-out                       | `taskport-events`     |
| **Schedule** | "fire something when" | N/A          | triggers another capability   | `taskport-scheduler`  |

## How to choose

- **Task** — a short-to-medium unit of application work you want off the
  request path: send an email, resize an image, recompute a cache. Usually one
  consumer runs it. In Taskport this *is* the official Django 6 Tasks API.
- **Job** — a finite, often heavy workload that belongs in its own process or
  container: ETL, GIS raster processing, tile generation, ML training, large
  imports, CPU/RAM-intensive batch. Exactly one execution per submission; you
  poll status, cancel, read logs.
- **Event** — a statement of fact with **zero or more** interested consumers:
  `resource.created`, `file.uploaded`, `dataset.updated`. Fan-out is the point.
  An event is *not* a command to run a specific function.
- **Schedule** — the "when". It does not contain business logic; it triggers a
  Task, a Job, or an Event at a time or on a cron.

## The critical distinction: Task vs Event

```text
Task  → one job to be executed        (imperative, ~1 consumer)
Event → a declaration that happened   (informational, 0..N consumers)
```

Publishing `order.placed` as an Event lets billing, inventory and analytics each
react independently. Enqueuing `charge_card` as a Task expresses "run this one
operation". Do not model a fan-out as a Task, and do not model a single command
as an Event.

## Composition, not merging

The concepts compose (`Task → Job`, `Event → Task`, `Schedule → Job`, ...) but
they are never merged into one abstraction. See
[composition.md](composition.md). Taskport deliberately does **not** provide DAG
workflows or a state machine — that is out of scope (section 46).
