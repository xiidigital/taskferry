# ADR-0014: Workspace with independently releasable packages

- Status: Accepted
- Date: 2026-07-24

## Context

The packages share a workspace for development while remaining independently
buildable, versioned, and releasable. Package boundaries also allow a distribution
to move to its own repository when that becomes useful.

## Decision

Develop in a **uv workspace** monorepo under `packages/*`. Enforce these rules to
keep the packages independently buildable:

1. **Self-contained packages.** Each package has its own `pyproject.toml`,
   `README.md`, `CHANGELOG.md`, `LICENSE`, `src/` and `tests/`.
2. **No monorepo-relative imports.** Packages import each other through published
   distribution names (`taskferry`), never via relative paths that assume the
   workspace layout. In the workspace this is wired with
   `[tool.uv.sources] taskferry = { workspace = true }`; outside it, the same
   import resolves from PyPI.
3. **Independent versioning (SemVer, no lockstep).** Each package versions on its
   own; inter-package dependencies use compatible ranges (`taskferry>=0.2,<0.3`),
   not `==` pins.
4. **Portable CI.** GitHub Actions workflows are per-package and copy cleanly to
   a standalone repo (see `.github/workflows/`).
5. **Trusted Publishing.** Releases use PyPI Trusted Publishing (OIDC); no API
   tokens are stored (section 40).

## Extraction procedure

```text
monorepo → git filter-repo the package subtree (preserving history)
         → move package to repo root
         → drop [tool.uv.sources] workspace pins (deps resolve from PyPI)
         → copy the package's CI workflow to .github/workflows/
         → configure PyPI Trusted Publishing for the new repo
         → tag & release independently
```

The full runnable checklist lives in `docs/family/extraction.md` and
`tooling/extract_package.sh` (dry-run only in this phase — nothing is published).

## Consequences

- The monorepo is a convenience, not a coupling.
- Extraction preserves git history and does not touch application-facing imports.
