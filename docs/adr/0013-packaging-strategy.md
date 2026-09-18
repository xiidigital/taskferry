# ADR-0013: One distribution per top-level module

- Status: Accepted
- Date: 2026-07-26
- History: replaces the 0.1 PEP 420 namespace-package approach (former ADR-0003)

## Context

0.1 used a PEP 420 namespace package: five distributions all contributing to
`taskferry.*` (`taskferry.core`, `taskferry.jobs`, `taskferry.django`, ...). It gave a
tidy import surface.

It also made `from taskferry import Taskferry` **structurally impossible**. A PEP 420
namespace package cannot contain an `__init__.py`, so no distribution could ship
the public API. And editable installs of namespace-sharing distributions break in
ways that are tedious to diagnose and depend on installer version.

## Decision

Every distribution owns its own top-level module.

```mermaid
flowchart BT
    TPD["taskferry-django<br/><code>taskferry_django</code>"]
    TPP["taskferry-procrastinate<br/><code>taskferry_procrastinate</code>"]
    TPC["taskferry-cloudtasks<br/><code>taskferry_cloudtasks</code>"]
    TPR["taskferry-cloudrun<br/><code>taskferry_cloudrun</code>"]
    TPJ["taskferry-jobs<br/><code>taskferry_jobs</code>"]
    TPE["taskferry-events<br/><code>taskferry_events</code>"]
    TPS["taskferry-scheduler<br/><code>taskferry_scheduler</code>"]
    TP["taskferry<br/><code>taskferry</code>"]

    TPD --> TP
    TPP --> TP
    TPC --> TP
    TPR --> TP
    TPJ --> TP
    TPE --> TP
    TPS --> TP
```

- `taskferry-core` is absorbed into `taskferry`. `taskferry.core` remains a valid
  import path, now provided by the `taskferry` distribution.
- `taskferry` declares **zero dependencies**, asserted by a test that reads the
  metadata as well as the source.
- Provider SDKs live in extras (`taskferry-jobs[aws]`), never in required
  dependencies, so installing an adapter never forces an SDK you do not use.
- Adapters advertise their backends through the `taskferry.backends` entry-point
  group, so configuration can name one without anything importing it eagerly.

## Alternatives considered

**Keep the namespace package and accept `taskferry.api` as the public surface.**
Preserves the tidy imports at the cost of an awkward extra module and the editable
install fragility. The brief explicitly requires `from taskferry import Taskferry`.
Rejected.

**One distribution with every adapter behind extras.** Simplest to release, and it
means `pip install taskferry` has to at least *know about* every provider, the
release cadence is shared, and a bug in the Azure adapter blocks a core release.
Rejected — independent versioning is the point.

**A single `taskferry` distribution plus one `taskferry-adapters` bundle.** Halfway
between the two, with the disadvantages of both. Rejected.

## Consequences

- `pip install taskferry` installs nothing else. This is the headline property, and
  it is tested three ways: source scan, runtime `sys.modules` check, and metadata.
- Import paths from 0.1 break: `taskferry.jobs` → `taskferry_jobs`,
  `taskferry.django` → `taskferry_django`. See
  [the migration guide](../migration/0.1-to-0.2.md).
- `taskferry_*` is slightly less elegant than `taskferry.*`. Accepted: a public API
  that exists beats a namespace that reads nicely.
- Each package remains independently buildable and extractable to its own
  repository, which [ADR-0014](0014-monorepo-to-multirepo.md) still describes.
