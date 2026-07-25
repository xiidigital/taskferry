"""Composed flow: HTTP request → Task → Event → Job, one correlation id.

    uv run python examples/composed-flow/run.py

Demonstrates composition by capability (not a workflow engine) and correlation
propagation across all four boundaries. Everything runs in-process here; in
production each hop would cross a real backend, carrying the same correlation.
"""

from __future__ import annotations

import os
import sys

import django
from django.conf import settings

sys.path.insert(0, os.path.dirname(__file__))

settings.configure(
    SECRET_KEY="example-only",
    USE_TZ=True,
    INSTALLED_APPS=[],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    TASKS={"default": {"BACKEND": "taskport.django.backends.local.LocalBackend"}},
)
django.setup()

import composed_app  # noqa: E402 - must follow django.setup()

from taskport.core import Correlation, use_correlation  # noqa: E402
from taskport.events import Event  # noqa: E402
from taskport.jobs import JobSpec, runners  # noqa: E402


def on_uploaded(event: Event) -> None:
    correlation = event.correlation
    assert correlation is not None
    print(f"  [event] file.uploaded corr={correlation.correlation_id} -> submit job")
    # Event → Job: a consumer submits a finite workload.
    handle = runners["default"].run(
        JobSpec(
            name="process-upload",
            command=[sys.executable, "-c", "print('    [job]   processing upload')"],
            correlation=correlation,
        )
    )
    result = runners["default"].wait(handle, timeout=10)  # type: ignore[attr-defined]
    job_corr = handle.correlation.correlation_id if handle.correlation else "?"
    print(f"  [job]   {handle.id} status={result.status} corr={job_corr}")


def main() -> None:
    composed_app.BUS.subscribe(on_uploaded, event_type="file.uploaded")

    # "HTTP request" begins a logical flow.
    correlation = Correlation.start()
    print(f"[request] upload received, corr={correlation.correlation_id}")
    with use_correlation(correlation):
        composed_app.handle_upload.enqueue("f-123")  # Task (runs inline locally)

    print(f"\nAll four steps shared correlation_id={correlation.correlation_id}")


if __name__ == "__main__":
    main()
