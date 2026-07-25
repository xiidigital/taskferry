# ADR-0001: Taskport is a family of ports & adapters, not a platform

- Status: Accepted
- Date: 2026-07-24

## Context

Asynchronous execution, background jobs, events and scheduling are needed by
most Python/Django systems, but the mechanisms differ wildly across traditional
infrastructure, cloud-native and serverless deployments. Existing tools
(Celery, Dramatiq, etc.) are *platforms*: they own the queue, the worker, the
protocol. Adopting one couples an application to that platform.

## Decision

Taskport is a **family of small, portable contracts (ports)** with
**provider adapters**, following dependency inversion:

```text
Application → Taskport contracts → Provider adapters → Infrastructure
```

Adapters depend on Taskport; Taskport never depends on a provider SDK. Taskport
does **not** reinvent queues, brokers, schedulers, workflow engines, event
systems, cloud infrastructure, observability, deployment, Kubernetes or
Terraform. It adapts the ones that already exist.

The family is split into independently versioned, independently releasable
distributions (`taskport-core`, `taskport-django`, `taskport-jobs`,
`taskport-events`, `taskport-scheduler`) developed together in a monorepo but
structured for clean extraction (see ADR-0010).

## Consequences

- Applications can move between PostgreSQL, Redis, GCP, AWS, Azure and
  Kubernetes by swapping an adapter and config, not rewriting code.
- Taskport must stay honest about provider differences (see ADR-0005) rather
  than inventing a lowest-common-denominator platform.
- We accept that Taskport is "less magic" than a full platform: it does not run
  workers for you, it makes the ones you have interchangeable.
