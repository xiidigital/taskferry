"""Guarantee: importing taskport.jobs never pulls in a provider SDK (section 31)."""

from __future__ import annotations

import subprocess
import sys


def test_importing_jobs_does_not_import_cloud_sdks() -> None:
    # Run in a fresh interpreter so other tests' imports don't pollute sys.modules.
    code = (
        "import sys; import taskport.jobs; "
        "import taskport.jobs.runners; "
        "bad = [m for m in ('boto3', 'google', 'azure', 'kubernetes') "
        "if m in sys.modules]; "
        "print(','.join(bad))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "", f"unexpectedly imported: {out.stdout.strip()}"
