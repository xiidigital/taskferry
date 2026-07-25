# ADR-0002: Task, Job, Event and Schedule are separate concepts

- Status: Accepted
- Date: 2026-07-24

## Context

It is tempting to collapse "do work later" into a single abstraction. Doing so
produces leaky APIs, because the four concepts have genuinely different
semantics, consumers and execution models.

## Decision

Model four distinct concepts and never force them into one abstraction:

| Concept  | Meaning              | Consumers     | Execution                     |
| -------- | -------------------- | ------------- | ----------------------------- |
| Task     | "do this operation"  | usually 1     | async work                    |
| Job      | "run this workload"  | 1             | process / container           |
| Event    | "this happened"      | 0..N          | fan-out                       |
| Schedule | "fire something when"| N/A           | triggers another capability   |

Each concept owns its domain types in its own package. `taskport-core` holds
only what is genuinely transversal (ADR-0004). A Task may *launch* a Job, an
Event may *trigger* a Task — but always by **composition**, never by merging the
abstractions (section 45). Taskport does not become a workflow/DAG engine
(ADR does not cover orchestration; see section 46 — explicitly out of scope).

## Consequences

- Five focused packages instead of one god-package.
- Clear vocabulary: "enqueue a Task", "run a Job", "publish an Event",
  "create a Schedule".
- Composition patterns are documented (`docs/family/composition.md`) but not
  auto-wired.
