# Taskport documentation

Aggregated documentation for the Taskport family. Each package also carries
docs that survive independent extraction (its own `README.md` + `CHANGELOG.md`).

## Architecture

- [Overview](architecture/overview.md) — ports & adapters, dependency graph,
  import-weight guarantee.

## The family

- [Task vs Job vs Event vs Schedule](family/concepts.md) — the four concepts and
  how to choose.
- [Composition patterns](family/composition.md) — Task → Job, Event → Task, etc.
- [Multicloud equivalence matrix](family/multicloud-matrix.md) — and the
  non-equivalences.
- [Deployment profiles](family/deployment-profiles.md) — minimal, Redis, GCP/AWS/
  Azure serverless, Kubernetes.
- [Roadmap & status](family/roadmap.md).
- [Extraction to multi-repo](family/extraction.md).

## Decisions (ADRs)

1. [Taskport is a family of ports & adapters](adr/0001-taskport-family.md)
2. [Separate Task/Job/Event/Schedule](adr/0002-separate-task-job-event-schedule.md)
3. [Namespace packages](adr/0003-namespace-packages.md)
4. [taskport-core scope](adr/0004-taskport-core-scope.md)
5. [Capability model](adr/0005-capability-model.md)
6. [Environment configuration](adr/0006-environment-configuration.md)
7. [Provider credentials](adr/0007-provider-credentials.md)
8. [Delivery semantics](adr/0008-delivery-semantics.md)
9. [Observability](adr/0009-observability.md)
10. [Monorepo to multi-repo](adr/0010-monorepo-to-multirepo.md)

## A documentation site

An aggregated MkDocs Material site (section 49) is on the roadmap; this
directory is already organized so `mkdocs` can consume it directly (nav mirrors
the structure above). It is intentionally not scaffolded with an unbuildable
config yet.
