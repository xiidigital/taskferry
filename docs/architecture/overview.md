# Taskport architecture overview

Taskport is a **ports & adapters** (hexagonal) layer. Applications depend on
small, stable Taskport contracts; provider adapters implement those contracts on
top of real infrastructure. Dependencies always point **inward**, toward the
contracts — never toward a provider SDK.

```text
        Application code
              │
              ▼
      Taskport contracts        (taskport.core + domain protocols)
              │
              ▼
      Provider adapters         (local, gcp, aws, azure, kubernetes)
              │
              ▼
        Infrastructure          (Cloud Tasks, SQS, Pub/Sub, Batch, K8s ...)
```

Correct direction:

```text
AWS adapter → core        GCP adapter → core        Azure adapter → core
```

Never:

```text
core → boto3        core → google.cloud        core → azure
```

## Package dependency graph

```text
taskport-django ─┐
taskport-jobs ───┤
taskport-events ─┼──▶ taskport-core        (core depends on nothing)
taskport-scheduler┘
```

The four domain packages **do not depend on each other** (section 56). An
application composes them; Taskport does not couple them. `taskport-jobs`,
`taskport-events` and `taskport-scheduler` do not require Django. Only
`taskport-django` requires Django (section 57).

## The four domains

```text
                       Taskport
                          │
        ┌─────────────────┼──────────────────┐
       Work             Events              Timing
        │                 │                   │
    ┌───┴───┐             │                   │
  Tasks    Jobs         Events            Scheduler
```

## Cross-cutting concerns (all in `taskport-core`)

```text
configuration · capabilities · identity · correlation
observability · errors · provider metadata · delivery semantics
```

Each is intentionally minimal and dependency-free. See
[ADR-0004](../adr/0004-taskport-core-scope.md).

## Import weight guarantee

`import taskport.jobs` must not import `boto3`, `google.cloud`, `azure`, or
`kubernetes`. Provider SDKs are imported **lazily**, inside the adapter that
needs them, at construction time — enforced by the `LazyRegistry` pattern and by
per-adapter local imports (section 31). Adapters live in their own submodules
(`taskport.jobs.runners.aws`, etc.) so importing the domain package never touches
a provider SDK.
