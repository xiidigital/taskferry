"""Importing taskport.django (and its backends) must not import a cloud SDK."""

from __future__ import annotations

import subprocess
import sys


def test_importing_django_backends_does_not_import_cloud_sdks() -> None:
    code = (
        "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','tests_settings'); "
        "import django; django.setup(); "
        "import sys, taskport.django, taskport.django.backends; "
        "bad=[m for m in ('boto3','google') if m in sys.modules]; "
        "print(','.join(bad))"
    )
    env = {**_env(), "PYTHONPATH": "packages/taskport-django/tests"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", f"unexpectedly imported: {out.stdout.strip()}"


def _env() -> dict[str, str]:
    import os

    return dict(os.environ)
