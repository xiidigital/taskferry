# Release process

Each distribution versions and releases **independently** — SemVer, no lockstep
(ADR-0014). A fix in the Azure adapter never blocks a `taskferry` release.

## Versioning

- **SemVer per distribution.** Bump the version in that package's `pyproject.toml`.
- Inter-package ranges stay compatible, not pinned: `taskferry>=0.2,<0.3`.
- Every release updates that package's `CHANGELOG.md` (Keep a Changelog style);
  mark breaking changes **BREAKING** with a migration note.

## Build

```bash
uv run python tooling/build_all.py     # wheel + sdist for every package into dist/
```

Each package builds in isolation, which is what proves the distributions are
separable. Verify a wheel installs clean and imports in a throwaway env before
publishing anything.

## Publish — Trusted Publishing (OIDC), no stored tokens

Publishing uses PyPI Trusted Publishing via GitHub Actions OIDC. **No API tokens
are stored anywhere.**

### In this monorepo

`.github/workflows/release.yml` publishes **one** distribution when a per-package
tag is pushed. The tag is `<distribution>-v<version>`:

```bash
git tag taskferry-v0.2.0        && git push origin taskferry-v0.2.0        # publishes taskferry
git tag taskferry-celery-v0.1.0 && git push origin taskferry-celery-v0.1.0 # publishes taskferry-celery
```

The workflow derives the package from the tag, runs `uv build --package <dist>`,
and publishes under a **GitHub Environment named after the distribution** (e.g.
`taskferry-celery`), then uploads with `pypa/gh-action-pypi-publish` over OIDC.

The per-distribution environment is what makes a monorepo work with Trusted
Publishing: PyPI refuses two *pending* publishers that share an identical
configuration (owner + repository + workflow + environment). Giving each
distribution its own environment keeps every publisher's claim set unique.

First release of each package (one-time, per distribution):

1. Create a **Trusted Publisher** on PyPI for the project (a *pending publisher*
   works before the project exists): <https://pypi.org/manage/account/publishing/>
   with — Owner `xiidigital`, Repository `taskferry`, Workflow `release.yml`,
   Environment `<distribution>` (the same name as the PyPI project, e.g.
   `taskferry-celery`).
2. Push the package's tag (above). Nothing publishes without that tag.

> The `taskferry` core was first published with a *blank* environment. Before its
> next release, add a second Trusted Publisher to the existing project with
> Environment `taskferry` so it matches this workflow.

### After extraction

The single-repo template is `tooling/ci-templates/release.yml` (triggers on a
GitHub Release). Use it once a package moves to its own repository.

## Extraction to a standalone repo

When an adapter graduates to its own repository, follow
[the extraction guide](../family/extraction.md): `tooling/extract_package.py`
previews what moves (dry-run only), history is preserved with `git filter-repo`,
and the package's CI/release templates in `tooling/ci-templates/` copy across
unchanged. The import path never changes, because each distribution already owns
its own top-level module.

## Release checklist

- [ ] `CHANGELOG.md` updated; version bumped.
- [ ] `uv run pytest`, `mypy`, `ruff`, `mkdocs build` all green.
- [ ] `tooling/build_all.py` produces a valid wheel + sdist.
- [ ] Wheel installs and imports in a clean env.
- [ ] Trusted Publisher configured for a first-time release.
