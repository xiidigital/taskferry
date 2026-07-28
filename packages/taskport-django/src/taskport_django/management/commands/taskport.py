"""``manage.py taskport`` — the same CLI, with Django's settings already loaded.

```mermaid
flowchart LR
    MC["manage.py taskport doctor"]
    CFG["settings.TASKPORT"]
    CLI["taskport.cli"]

    MC --> CFG --> CLI
```

Taskport's own ``taskport`` command reads ``TASKPORT_*`` environment variables.
Inside a Django project the configuration usually lives in settings instead, so
this command bridges the two: it loads settings, writes the resolved
configuration to a temporary JSON file, and hands it to the same CLI.

It is a wrapper on purpose. The CLI is the primary interface — it works in a
container that has never heard of Django — and duplicating its argument parsing
here would guarantee the two drift apart.

    python manage.py taskport doctor
    python manage.py taskport backends
    python manage.py taskport capabilities pg
    python manage.py taskport status task_9f2c... --backend pg
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from taskport.cli import main as cli_main


class Command(BaseCommand):
    help = "Run a Taskport CLI subcommand against this project's settings."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "args",
            nargs="*",
            help="Arguments passed straight through to the taskport CLI.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        argv = list(options.get("args") or [])
        if not argv:
            argv = ["doctor"]

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "taskport.json"
            config_path.write_text(json.dumps(self._config_payload()), encoding="utf-8")
            exit_code = cli_main(["--config", str(config_path), *argv])

        if exit_code != 0:
            raise CommandError(f"taskport {' '.join(argv)} exited with status {exit_code}")

    def _config_payload(self) -> dict[str, Any]:
        """The raw TASKPORT setting, or the local default when it is absent."""
        from django.conf import settings

        raw = getattr(settings, "TASKPORT", None)
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
