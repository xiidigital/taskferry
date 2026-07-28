"""Importing taskport_scheduler must not pull in a provider SDK (section 31)."""

from __future__ import annotations

import subprocess
import sys


def test_importing_scheduler_does_not_import_cloud_sdks() -> None:
    code = (
        "import sys, taskport_scheduler, taskport_scheduler.adapters; "
        "bad=[m for m in ('google','boto3','azure','kubernetes') "
        "if m in sys.modules]; print(','.join(bad))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "", f"unexpectedly imported: {out.stdout.strip()}"
