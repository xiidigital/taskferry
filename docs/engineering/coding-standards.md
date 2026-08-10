# Coding standards

What the tooling enforces, and the conventions it cannot.

## Enforced by tooling

- **Formatting & lint: `ruff`.** `ruff format` is authoritative (line length 100,
  target py312). `ruff check` runs `E, F, I, UP, B, SIM, RUF`. Config lives in the
  root `pyproject.toml`; every package inherits it.
- **Types: `mypy --strict`** over `packages/*/src`. Shipped code is fully typed,
  because that is the contract a user's own type checker sees after installing a
  distribution. Tests are excluded deliberately (SDK doubles and untyped pytest
  fixtures would produce noise, not safety).
- **`py.typed`** ships in every distribution (PEP 561).

Run all three before pushing:

```bash
uv run ruff format . && uv run ruff check . && uv run mypy packages/*/src
```

## Conventions the tools can't check

- **Immutability.** Domain types are frozen dataclasses (`ExecutionSpec`,
  `Execution`, `ExecutionResult`, `CapabilitySet`). Evolve with `replace`/`evolve`,
  never mutate. Mutable state (a backend's execution store) is private and
  lock-guarded.
- **Small, readable modules.** The four local backends are written to be "readable
  in one sitting." Some duplication between them is accepted over an abstraction
  that hides how each engine actually behaves. Extract shared logic only when it is
  genuinely identical and pure (e.g. `ExecutionResult.from_exception`).
- **Honest capabilities (ADR-0005).** A backend advertises what it can do and
  raises `UnsupportedCapability` for the rest. Never accept an option and silently
  ignore it — that is the one failure mode the whole model exists to prevent.
- **Lazy provider imports.** An adapter imports its SDK *inside* the method that
  needs it, never at module top level, so `import taskport_jobs` costs nothing
  until a cloud backend is actually built. There is a test for this.
- **`taskport` has zero dependencies.** Enforced three ways (source scan, runtime
  `sys.modules` check, metadata). Nothing in `packages/taskport` may import a
  third-party package.
- **Docstrings explain *why*.** Module docstrings state what the thing is, what it
  is deliberately *not*, and the honest limitations (delivery guarantee, what
  survives a restart). Mermaid diagrams are welcome and render in the docs site.

## Errors

Raise the narrowest Taskport error (`SubmissionError`, `ExecutionNotFound`,
`UnsupportedCapability`, `ConfigurationError`, `ProviderError`), never a bare
`Exception`. Adapters wrap provider SDK errors in `ProviderError` (chaining with
`from`) so callers depend on a stable type.
