# Release process

Each distribution versions and releases **independently** — SemVer, no lockstep
(ADR-0014). A fix in the Azure adapter never blocks a `taskport` release.

## Versioning

- **SemVer per distribution.** Bump the version in that package's `pyproject.toml`.
- Inter-package ranges stay compatible, not pinned: `taskport>=0.2,<0.3`.
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
are stored anywhere.** The template is `tooling/ci-templates/release.yml`; it
triggers on a GitHub Release and needs `permissions: id-token: write`.

First release of a package (manual, one-time, done by a maintainer):

1. Register the project name on PyPI.
2. Configure the Trusted Publisher (owner, repo, `release.yml`, environment).
3. Create a GitHub Release tagged for that package — the workflow builds and
   publishes.

Nothing here publishes automatically from the monorepo without a maintainer
creating that Release.

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
