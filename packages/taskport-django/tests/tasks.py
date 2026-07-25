"""Module-level tasks used by the taskport-django tests.

Defined at module level so they satisfy Django's ``is_module_level_function``
validation and can be resolved from a portable message.
"""

from __future__ import annotations

from django.tasks import task

from taskport.core import current_correlation

# Records side effects so execution can be asserted.
CALLS: list[object] = []
SEEN_CORRELATIONS: list[str | None] = []


@task
def capture_correlation(marker: object) -> object:
    active = current_correlation()
    SEEN_CORRELATIONS.append(active.correlation_id if active else None)
    return marker


@task
def record(value: object) -> object:
    CALLS.append(value)
    return value


@task
def add(a: int, b: int) -> int:
    result = a + b
    CALLS.append(result)
    return result
