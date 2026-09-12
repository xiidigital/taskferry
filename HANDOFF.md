# Taskport — delivery handoff

A portable execution layer for Python: model a unit of work once, route it to an
engine that already exists (Procrastinate, Celery, Cloud Tasks, SQS, Service Bus,
Cloud Run Jobs, Kubernetes, …) by configuration rather than code change.

- **Status:** finalized and green. Nothing is published; the repository is
  self-contained and ready to hand off.
- **Verified:** `954 passed, 28 skipped`; `mypy --strict` clean (106 source
  files); `ruff` lint + format clean; `mkdocs build` clean; all 13 distributions
  build a wheel + sdist and install/import from the built artifacts.

## What ships (13 independently-releasable distributions)

| Distribution | Version | Import | Role |
| --- | --- | --- | --- |
| `taskport` | 0.2.0 | `taskport` | the execution layer — **zero dependencies** |
| `taskport-procrastinate` | 0.2.0 | `taskport_procrastinate` | task backend (PostgreSQL) |
| `taskport-celery` | 0.1.0 | `taskport_celery` | task backend (Celery) |
| `taskport-cloudtasks` | 0.2.0 | `taskport_cloudtasks` | task backend (GCP Cloud Tasks, push) |
| `taskport-sqs` | 0.2.0 | `taskport_sqs` | task backend (AWS SQS) |
| `taskport-servicebus` | 0.2.0 | `taskport_servicebus` | task backend (Azure Service Bus) |
| `taskport-dramatiq` | 0.2.0 | `taskport_dramatiq` | task backend (Dramatiq) |
| `taskport-cloudrun` | 0.2.0 | `taskport_cloudrun` | job backend (Cloud Run Jobs) |
| `taskport-jobs` | 0.2.0 | `taskport_jobs` | job backends (AWS Batch, Kubernetes, Azure Container Apps) |
| `taskport-events` | 0.2.0 | `taskport_events` | pub/sub events (in-memory, Pub/Sub, SNS/EventBridge, Event Grid, Kafka) |
| `taskport-scheduler` | 0.2.0 | `taskport_scheduler` | schedules (local, Cloud Scheduler, EventBridge, CronJob) |
| `taskport-otel` | 0.1.0 | `taskport_otel` | OpenTelemetry bridge |

Every adapter depends only on `taskport` and puts its provider SDK behind an
extra; an architectural test enforces that importing an adapter pulls in no SDK.

## Run it yourself

```bash
uv sync --all-packages                                   # one venv, everything editable
PATH="$PWD/.venv/bin:$PATH" uv run pytest                # 954 passed (needs `python` on PATH for subprocess-job tests)
uv run ruff check . && uv run ruff format --check .      # lint + format
uv run mypy packages/*/src                               # strict types, 106 files
uv run mkdocs build                                      # documentation site
uv run python tooling/build_all.py                       # 13 wheels + sdists into dist/
uv run python examples/jobs-local/run.py                 # a runnable example
```

## Where things are

- **Code:** `packages/*/src` — each distribution self-contained (own
  `pyproject.toml`, `README.md`, `CHANGELOG.md`, `LICENSE`, `src/`, `tests/`,
  `py.typed`).
- **Docs:** `docs/` — architecture, the ADR set (`docs/adr/`, 0001–0015 with an
  index + history in `docs/adr/README.md`), engineering process
  (`docs/engineering/`), and the roadmap (`docs/family/roadmap.md`).
- **Tooling:** `tooling/build_all.py` (build every package) and
  `tooling/extract_package.py` (dry-run preview of extracting a package to its
  own repo). CI templates in `tooling/ci-templates/`.

## Remaining steps — these need your accounts (not done here)

Nothing below has been performed; publishing requires your authorization and
credentials. See `docs/engineering/release.md` for detail.

1. **Push** the repository to your Git remote (`git push -u origin main`).
2. **Per distribution**, when you choose to release it:
   - register the project name on PyPI;
   - configure PyPI **Trusted Publishing** (OIDC) for the repo/workflow
     (`tooling/ci-templates/release.yml`);
   - create a GitHub Release, which triggers the OIDC publish — no tokens stored.

## Roadmap (optional, not blocking)

Tracked in `docs/family/roadmap.md`. Open items: natively-async adapters (a
performance refinement — the async surface already works via threads), and log
streaming for the *cloud* job backends (the local backend already streams).
