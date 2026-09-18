"""The Django ``AppConfig``.

Add ``"taskferry_django"`` to ``INSTALLED_APPS`` and you get the system checks and
a clean shutdown. That is all it does — no autodiscovery, no import-time scanning
of your task modules, no global registry mutated by a side effect. Configuration
is read when the runtime is first needed, from the setting, explicitly.
"""

from __future__ import annotations

from typing import Any

from django.apps import AppConfig
from django.core.checks import register
from django.core.signals import setting_changed


class TaskferryConfig(AppConfig):
    """Registers Taskferry's system checks and keeps the runtime in step."""

    name = "taskferry_django"
    label = "taskferry"
    verbose_name = "Taskferry"

    def ready(self) -> None:
        from .checks import check_taskferry

        register(check_taskferry)
        # A test using override_settings(TASKFERRY=...) must get a runtime built
        # from the new settings, not the one cached before the override.
        setting_changed.connect(_reset_on_setting_change)


def _reset_on_setting_change(sender: Any = None, setting: str = "", **kwargs: Any) -> None:
    if setting in {"TASKFERRY", "TASKS"}:
        from .config import reset_runtime

        reset_runtime()


__all__ = ["TaskferryConfig"]
