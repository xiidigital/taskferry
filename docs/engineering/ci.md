# CI

CI runs the same four gates you run locally, on every push and pull request. The
workflow is `.github/workflows/ci.yml`; it uses `astral-sh/setup-uv` and
`uv sync --all-packages`.

## Jobs

| Job       | What it runs                                             | Guards |
| --------- | ------------------------------------------------------- | ------ |
| `quality` | `ruff check` + `ruff format --check`, `mypy packages/*/src`, `pytest --cov` | lint, format, types, behaviour |
| `build`   | `tooling/build_all.py` (wheel + sdist per package), uploads `dist/` | packaging & separability |
| `docs`    | `mkdocs build`                                           | the docs site still builds |

`quality` runs on a Python matrix (3.12, 3.13). `build` and `docs` depend on it.

## Why these and not more

The gates map one-to-one to the project's invariants:

- **lint + format** — style is not a review topic; `ruff` decides it.
- **types** — strict mypy on shipped code is the contract a user's checker sees.
- **tests** — the contract suites and architecture tests are where correctness and
  the "zero dependencies / no eager SDK import" rules are actually enforced.
- **build** — building each package independently is what proves ADR-0014's
  separability claim, not just asserts it.
- **docs** — a broken link or a missing ADR fails the build, so the documentation
  cannot silently rot.

## Per-package CI after extraction

An extracted repository does not inherit the monorepo workflow. It copies
`tooling/ci-templates/package-ci.yml` (the same gates, scoped to one package) and
`tooling/ci-templates/release.yml` (Trusted Publishing). Both are kept in sync
with the root workflow so extraction is a copy, not a rewrite.

## Reproducing a CI failure locally

```bash
uv sync --all-packages
uv run ruff check . && uv run ruff format --check .
uv run mypy packages/*/src
uv run pytest --cov --cov-report=term-missing
uv run mkdocs build
```
