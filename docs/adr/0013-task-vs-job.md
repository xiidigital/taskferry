# ADR-0013: Task and Job are separate primitives

- Status: Accepted
- Date: 2026-07-26
- Refines: [ADR-0002](0002-separate-task-job-event-schedule.md)

## Context

Both a Task and a Job are "work that happens elsewhere, later". It is tempting to
model a Job as a Task with a longer timeout and a bigger machine.

## Decision

They stay separate primitives, with separate specs, separate ports and separate
capability profiles.

| | Task | Job |
| --- | --- | --- |
| identified by | a Python function name | a container image or an argv |
| carries | JSON arguments | env, resources, working directory |
| returns | a Python value (sometimes) | an exit code |
| runs in | a shared worker | its own process or container |
| resources | the worker's | its own CPU/memory/GPU envelope |
| typical engine | Procrastinate, Cloud Tasks, Celery | Cloud Run Jobs, K8s, AWS Batch |

A third primitive, **Inline**, models "run this here, now" — the only one that may
carry a live callable, because nothing crosses a process boundary.

## Alternatives considered

**One `WorkSpec` with optional container fields.** The fields would be meaningless
for most engines, `required_capabilities()` would become a maze of conditionals,
and every task backend would have to decide what to do with an `image`. Worse, the
lowest common denominator would push toward accepting-and-ignoring, which is the
failure mode the capability model exists to prevent.

**One spec plus a `mode` discriminator.** Same problems as above with an extra
field to keep consistent. Type checkers could not narrow the valid fields, so the
IDE would offer `image` on a Procrastinate task forever.

**Model Jobs as Tasks whose function launches a container.** This is what
applications do today without Taskport, and it is precisely the coupling being
removed: the application ends up holding a Kubernetes client.

## Consequences

- `runtime.tasks` and `runtime.jobs` are separate facades with different
  signatures, so an IDE offers only fields that mean something.
- Routing keys differ by design: tasks route on `queue`, jobs route on `profile`.
- A backend declares one `kind` and rejects specs of another with a `TypeError`
  naming both, before anything is sent.
- Some duplication between `TaskSpec` and `JobSpec` is accepted. The shared
  portable fields live on `ExecutionSpec`; the rest is deliberately not shared.
