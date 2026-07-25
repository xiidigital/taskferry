# Roadmap & status

Versioning is per-package SemVer with **no lockstep** (section 28). This is an
early foundation: contracts are real, a local provider always exists, and at
least one real cloud adapter ships per domain. Remaining adapters are on the
roadmap and gated behind extras so they never bloat a default install.

## taskport-core — 0.1.0 (functional)

Complete for the current scope. Tiny, dependency-free, >=90% covered.

## taskport-django — 0.1.0 (functional)

- ✅ Contract: works behind the official `django.tasks` API + `TASKS` setting.
- ✅ Local/dev: passthrough over Django's `ImmediateBackend`.
- ✅ Serverless: **Cloud Tasks** push backend (first-class).
- ✅ Traditional: **SQS** backend (send side).
- 🔜 Procrastinate (PostgreSQL), Dramatiq (Redis), Azure Service Bus backends.
- 🔜 Consumer/worker helpers for the SQS pull side.

## taskport-jobs — 0.1.0 (functional)

- ✅ `LocalJobRunner` (real subprocess execution: status, cancel, timeout, env).
- ✅ `CloudRunJobsRunner` (GCP), `BatchRunner` (AWS), `ContainerAppsJobsRunner`
  (Azure), `KubernetesJobRunner` — adapter code with lazy SDK imports, tested
  with injected fake clients.
- 🔜 Log streaming, array/indexed parallelism per provider, GPU scheduling
  refinements.

## taskport-events — 0.1.0 (functional)

- ✅ `Event` model (CloudEvents-inspired), `EventPublisher`/`EventSubscriber`
  contracts, capability model.
- ✅ `InMemoryEventBus` (real fan-out pub/sub for tests and single-process use).
- ✅ `PubSubPublisher` (GCP) reference adapter (lazy import, fake-tested).
- 🔜 EventBridge/SNS (AWS), Event Grid (Azure), Kafka, NATS, RabbitMQ.
- 🔜 Replay and retention helpers where providers support them.

## taskport-scheduler — 0.1.0 (functional)

- ✅ `Schedule` model + `CronTrigger`/`IntervalTrigger`/`OneShotTrigger`,
  `ScheduleTarget` (Task/Job/Event), capability model.
- ✅ `LocalScheduler` (real in-process cron/interval/one-shot firing).
- ✅ `CloudSchedulerScheduler` (GCP) reference adapter (lazy import, fake-tested).
- 🔜 EventBridge Scheduler (AWS), Azure scheduling, Kubernetes CronJob adapters.

## Cross-cutting

- ✅ Capability model, correlation, JSON serialization, delivery vocabulary,
  no-op tracer + OTel-shaped hooks.
- 🔜 OpenTelemetry bridge implementation behind the `otel` extras.
- 🔜 MkDocs Material documentation site aggregating per-package docs (section 49).
