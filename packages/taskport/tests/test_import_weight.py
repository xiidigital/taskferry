"""Importing an adapter must not import its provider SDK.

`taskport` has its own three-way guard in `test_architecture.py`. This file
guards the other direction, one level out: an adapter is allowed to *depend* on
`boto3`, and it is not allowed to **import** it just because someone imported the
package.

That distinction is what keeps a web process cheap. A Django app with
`taskport-jobs` installed for a nightly Kubernetes job should not pay for the
Kubernetes client on every worker boot, and a process that only enqueues tasks
should never load the Google SDK.

```mermaid
flowchart LR
    I["import taskport_jobs"]
    OK["module loads<br/>no SDK in sys.modules"]
    LATER["backend.submit()"]
    SDK["boto3 imported here, and only here"]

    I --> OK
    OK --> LATER --> SDK
```

Each check runs in a **subprocess**, because this test session has already
imported plenty and `sys.modules` would be useless otherwise.

0.1 had one of these per package. The 0.2 refactor dropped most of them while
moving the packages; this restores the coverage for every adapter at once, so a
new adapter is included the moment it is added to `ADAPTERS`.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

#: Every adapter, and the SDKs it must not import at module load. An adapter with
#: no SDK still appears, because "imports nothing heavy" is worth asserting even
#: when the list is short — it catches a future dependency added carelessly.
ADAPTERS: dict[str, tuple[str, ...]] = {
    "taskport_procrastinate": ("procrastinate", "psycopg", "psycopg2", "asyncpg"),
    "taskport_cloudtasks": ("google", "grpc"),
    "taskport_cloudrun": ("google", "grpc"),
    "taskport_sqs": ("boto3", "botocore"),
    "taskport_servicebus": ("azure", "uamqp"),
    "taskport_dramatiq": ("dramatiq", "redis", "pika"),
    "taskport_celery": ("celery", "kombu", "redis", "amqp"),
    # Not a backend adapter: taskport-otel legitimately imports opentelemetry
    # (its whole purpose). It must still not drag in a provider SDK.
    "taskport_otel": ("boto3", "botocore", "google", "azure", "kubernetes"),
    "taskport_jobs": ("boto3", "botocore", "kubernetes", "azure"),
    "taskport_events": ("google", "boto3", "botocore", "azure", "confluent_kafka"),
    "taskport_scheduler": ("google", "boto3", "botocore", "kubernetes"),
    # Django is a *hard* dependency here, so importing it is expected and is not
    # on this list. A provider SDK still is not: the bridge talks to engines
    # through taskport, never directly.
    "taskport_django": ("boto3", "botocore", "google", "azure", "kubernetes", "procrastinate"),
}

#: Submodules that must also be import-light. Importing the module that *contains*
#: a backend is the common case in a settings file or an entry-point load, so it
#: matters as much as importing the package.
SUBMODULES: dict[str, tuple[str, ...]] = {
    "taskport_jobs": ("taskport_jobs.aws", "taskport_jobs.kubernetes", "taskport_jobs.azure"),
    "taskport_sqs": ("taskport_sqs.backend", "taskport_sqs.consumer"),
    "taskport_servicebus": ("taskport_servicebus.backend", "taskport_servicebus.consumer"),
    "taskport_dramatiq": ("taskport_dramatiq.backend", "taskport_dramatiq.worker"),
    "taskport_celery": ("taskport_celery.backend", "taskport_celery.worker"),
    "taskport_cloudtasks": ("taskport_cloudtasks.backend", "taskport_cloudtasks.receiver"),
    "taskport_cloudrun": ("taskport_cloudrun.backend",),
    "taskport_procrastinate": (
        "taskport_procrastinate.backend",
        "taskport_procrastinate.worker",
    ),
    "taskport_django": ("taskport_django.backend", "taskport_django.execute"),
}


def _probe(modules: tuple[str, ...], forbidden: tuple[str, ...]) -> list[str]:
    """Import ``modules`` in a clean interpreter; return the forbidden leaks."""
    # taskport_django touches django.conf at import time, so the probe configures
    # a minimal settings module first. Everything else ignores this.
    preamble = (
        "import django\n"
        "from django.conf import settings\n"
        "settings.configure(INSTALLED_APPS=[], DATABASES={}, USE_TZ=True)\n"
        "django.setup()\n"
        if any(module.startswith("taskport_django") for module in modules)
        else ""
    )
    script = (
        "import sys\n"
        + preamble
        + "".join(f"import {module}\n" for module in modules)
        + f"forbidden = {forbidden!r}\n"
        "print(','.join(sorted(m for m in forbidden if m in sys.modules)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        pytest.skip(f"{modules[0]} is not installed in this environment")
    return [name for name in result.stdout.strip().split(",") if name]


@pytest.mark.parametrize(("package", "forbidden"), sorted(ADAPTERS.items()))
def test_importing_an_adapter_imports_no_sdk(package: str, forbidden: tuple[str, ...]) -> None:
    leaked = _probe((package,), forbidden)
    assert not leaked, (
        f"`import {package}` transitively imported {leaked}. Every provider SDK must be "
        f"imported lazily, inside the method that builds a client — otherwise a process "
        f"that never touches this provider still pays for its SDK."
    )


@pytest.mark.parametrize(("package", "submodules"), sorted(SUBMODULES.items()))
def test_importing_a_backend_module_imports_no_sdk(
    package: str, submodules: tuple[str, ...]
) -> None:
    """The path a settings file or an entry-point load actually takes."""
    leaked = _probe(submodules, ADAPTERS[package])
    assert not leaked, (
        f"importing {list(submodules)} transitively imported {leaked}. A configuration "
        f"naming this backend must not load the SDK until a client is built."
    )


def test_every_adapter_distribution_is_covered() -> None:
    """A new adapter must not slip past this file unnoticed."""
    from pathlib import Path

    packages_dir = Path(__file__).resolve().parents[2]
    distributions = {
        path.name.replace("-", "_")
        for path in packages_dir.iterdir()
        if path.is_dir() and (path / "pyproject.toml").exists() and path.name != "taskport"
    }
    missing = distributions - set(ADAPTERS)
    assert not missing, (
        f"these adapter distributions have no import-weight check: {sorted(missing)}. "
        f"Add them to ADAPTERS with the SDKs they must not import eagerly."
    )


def test_importing_every_adapter_together_stays_light() -> None:
    """The realistic worst case: a project that installs all of them.

    Individually-lazy imports can still add up if one adapter imports another's
    module at load time. This is the check that would catch that.
    """
    every_sdk = tuple({sdk for sdks in ADAPTERS.values() for sdk in sdks})
    leaked = _probe(tuple(sorted(ADAPTERS)), every_sdk)
    assert not leaked, (
        f"importing every adapter at once pulled in {leaked}; one of them is importing "
        f"an SDK — or another adapter — at module load."
    )
