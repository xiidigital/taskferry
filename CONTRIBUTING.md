# Contributing to Taskferry

Thanks for helping. This page gets you from a clone to a green test run; the
detail lives under [`docs/engineering/`](docs/engineering).

## Prerequisites

- **Python 3.12+** (Django 6 requires it; the whole workspace targets 3.12).
- **[uv](https://docs.astral.sh/uv/)** — the workspace and dev tooling are wired
  around it.

## Setup

```bash
git clone <repo> taskferry && cd taskferry
uv sync --all-packages        # one venv, every distribution editable
```

That single environment contains `taskferry` plus all ten adapter distributions
and the dev tooling (pytest, mypy, ruff, mkdocs-material). No per-package venvs.

## The layout

```text
packages/
  taskferry/                 the execution layer — zero dependencies, ever
  taskferry-<provider>/      one adapter distribution per top-level module
docs/                       architecture, engineering (this), ADRs
examples/                   runnable end-to-end samples
tooling/                    build + extraction helpers
```

Each `packages/*` is independently buildable and releasable, and structured to be
extracted to its own repository without edits (ADR-0014). It follows that an
adapter may depend on `taskferry` but **never** on another adapter, and `taskferry`
depends on nothing.

## The loop

```bash
uv run pytest                     # the whole suite (~900 tests, ~10s)
uv run ruff check . && uv run ruff format .
uv run mypy packages/*/src        # strict, on shipped code
uv run mkdocs build               # docs still build
```

All four must pass before a change is ready. What each one enforces, and why, is
in [coding standards](docs/engineering/coding-standards.md),
[testing](docs/engineering/testing.md) and [CI](docs/engineering/ci.md).

## Making a change

1. Branch off `main`.
2. If it is a new backend, start from
   [adding an adapter](docs/engineering/adding-an-adapter.md) — the contract suite
   tells you when it is done.
3. If it changes a public decision, add or update an
   [ADR](docs/adr/README.md) in the same PR.
4. Keep the four checks green. Add tests for behaviour, not for coverage.
5. Use Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`,
   `chore:`), and update the affected package's `CHANGELOG.md`.

## What not to add

Taskferry is a portability layer, not an execution engine. Queues, worker daemons,
schedulers-as-daemons, brokers and workflow runtimes are out of scope on purpose —
see [ADR-0002](docs/adr/0002-not-a-task-queue.md) before proposing one.
