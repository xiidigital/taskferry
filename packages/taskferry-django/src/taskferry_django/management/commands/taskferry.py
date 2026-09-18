"""``manage.py taskferry`` — the same CLI, with Django's settings already loaded.

```mermaid
flowchart LR
    MC["manage.py taskferry doctor"]
    CFG["settings.TASKFERRY"]
    CLI["taskferry.cli"]

    MC --> CFG --> CLI
```

Taskferry's own ``taskferry`` command reads ``TASKFERRY_*`` environment variables.
Inside a Django project the configuration usually lives in settings instead, so
this command bridges the two: it loads settings, writes the resolved
configuration to a temporary JSON file, and hands it to the same CLI.

It is a wrapper on purpose. The CLI is the primary interface — it works in a
container that has never heard of Django — and duplicating its argument parsing
here would guarantee the two drift apart.

    python manage.py taskferry doctor
    python manage.py taskferry backends
    python manage.py taskferry capabilities pg
    python manage.py taskferry status task_9f2c... --backend pg
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from taskferry.cli import main as cli_main


class Command(BaseCommand):
    help = "Run a Taskferry CLI subcommand against this project's settings."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "args",
            nargs="*",
            help="Arguments passed straight through to the taskferry CLI.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        argv = list(options.get("args") or [])
        if not argv:
            argv = ["doctor"]

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "taskferry.json"
            config_path.write_text(json.dumps(self._config_payload()), encoding="utf-8")
            exit_code = cli_main(["--config", str(config_path), *argv])

        if exit_code != 0:
            raise CommandError(f"taskferry {' '.join(argv)} exited with status {exit_code}")

    def _config_payload(self) -> dict[str, Any]:
        """The raw TASKFERRY setting, or the local default when it is absent."""
        from django.conf import settings

        raw = getattr(settings, "TASKFERRY", None)
        if isinstance(raw, dict):
            return raw
        return {
            "backends": {
                "inline": {"factory": "inline"},
                "thread": {"factory": "thread"},
                "subprocess": {"factory": "subprocess"},
            },
            "defaults": {"inline": "inline", "task": "thread", "job": "subprocess"},
        }
