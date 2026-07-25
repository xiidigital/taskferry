# ADR-0003: PEP 420 native namespace packages under `taskport.*`

- Status: Accepted
- Date: 2026-07-24

## Context

We want five separately-distributed packages to share one import namespace
(`taskport.core`, `taskport.django`, `taskport.jobs`, `taskport.events`,
`taskport.scheduler`) while remaining independently installable and extractable
to separate repositories.

Options considered:

1. **Distinct top-level packages** (`taskport_core`, `taskport_jobs`, ...) — ugly
   imports, no shared brand namespace.
2. **`pkg_resources`/`pkgutil`-style namespace packages** — legacy, requires
   `__init__.py` shims, deprecated tooling.
3. **PEP 420 native (implicit) namespace packages** — modern, no `__init__.py` at
   the namespace root, first-class support in setuptools/hatchling/uv.

## Decision

Use **PEP 420 native namespace packages**. Each distribution ships part of the
`taskport` namespace via a `src/` layout:

```text
packages/taskport-core/src/taskport/core/__init__.py     # regular subpackage
packages/taskport-jobs/src/taskport/jobs/__init__.py
...
```

There is **no** `src/taskport/__init__.py` anywhere. `taskport` is an implicit
namespace; `taskport.core` etc. are regular packages that own their modules.

Build backend: **hatchling**, configured with
`[tool.hatch.build.targets.wheel] packages = ["src/taskport"]`, which packages
the namespaced subtree without an `__init__.py` at the root.

Each package ships `py.typed` (PEP 561) so type information survives installation.

## Consequences

- Clean imports: `from taskport.jobs import runners`.
- Independent install: any subset of packages coexists in one environment.
- Editable installs and wheels both work (verified with `uv sync` + `hatchling`).
- Extraction to a separate repo is a directory move — the namespace layout is
  identical inside or outside the monorepo (ADR-0010).
- Caveat: never add `taskport/__init__.py`; doing so would shadow the namespace
  and break coexistence. Enforced by review and by the extraction checklist.
