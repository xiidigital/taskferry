# Extracting a package to its own repository

This is the runnable companion to [ADR-0014](../adr/0014-monorepo-to-multirepo.md).
**Nothing here is executed during the current phase** — no repos are created and
nothing is published. This documents the procedure and the dry-run tooling.

## Preconditions (already satisfied by design)

- The package is self-contained: `pyproject.toml`, `README.md`, `CHANGELOG.md`,
  `LICENSE`, `src/`, `tests/`.
- No monorepo-relative imports; inter-package deps are distribution names with
  compatible ranges.
- A portable CI workflow exists in `.github/workflows/`.

## Procedure

```bash
# 1. Preview what would move (safe, read-only).
python tooling/extract_package.py taskferry-jobs --dry-run

# 2. Extract the subtree preserving git history (requires git-filter-repo).
#    Run inside a fresh clone; this rewrites history.
git clone <monorepo> taskferry-jobs && cd taskferry-jobs
git filter-repo --subdirectory-filter packages/taskferry-jobs

# 3. Drop the workspace source pin so deps resolve from PyPI.
#    Remove the matching line(s) from [tool.uv.sources] if present.

# 4. Add the standalone CI workflow.
mkdir -p .github/workflows
cp path/to/ci-templates/package-ci.yml .github/workflows/ci.yml

# 5. Configure PyPI Trusted Publishing (OIDC) for the new repo, then tag.
git tag taskferry-jobs-v0.1.0 && git push --tags
```

## What to verify after extraction

- `uv build` produces a valid wheel + sdist.
- `pip install dist/*.whl` in a clean venv, then `import taskferry_jobs` works.
- `pytest` passes against the installed package.
- No `taskferry/__init__.py` was introduced (would break the namespace, ADR-0013).

## Manual actions the maintainer must perform (require authorization)

These are intentionally **not** automated and require explicit human action:

1. Create the GitHub repository `github.com/<org>/taskferry-<name>`.
2. Push the extracted history.
3. Register the project name on PyPI and configure Trusted Publishing.
4. Create the first GitHub Release, which triggers the OIDC publish workflow.
