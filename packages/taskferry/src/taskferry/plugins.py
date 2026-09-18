"""Backend discovery — entry points, not import-time magic.

An adapter distribution advertises its backends in its own ``pyproject.toml``::

    [project.entry-points."taskferry.backends"]
    procrastinate = "taskferry_procrastinate:make_backend"

which lets a deployment write ``{"factory": "procrastinate", ...}`` in config and
have it work the moment the package is installed — without `taskferry` importing,
knowing about, or depending on that package.

```mermaid
flowchart BT
    PRO["taskferry-procrastinate<br/>entry point: procrastinate"]
    CR["taskferry-cloudrun<br/>entry point: cloudrun"]
    CT["taskferry-cloudtasks<br/>entry point: cloudtasks"]
    EP["taskferry.backends<br/>entry-point group"]
    TP["taskferry<br/>load_backend()"]

    PRO --> EP
    CR --> EP
    CT --> EP
    EP --> TP
```

Two things this deliberately does *not* do. It does not scan installed packages
looking for anything importable, and it does not import every registered plugin
at startup: an entry point is only loaded when a backend that names it is
actually built. A process that enqueues tasks but never runs jobs never imports
the Google SDK.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from functools import cache
from typing import Any, cast

from .config import DEFAULT_BACKENDS
from .core.errors import ConfigurationError
from .ports import ExecutionBackend

ENTRY_POINT_GROUP = "taskferry.backends"

BackendFactory = Callable[..., ExecutionBackend]
"""A callable taking the configured options as keyword arguments."""

_manual: dict[str, BackendFactory] = {}


def register_backend(name: str, factory: BackendFactory, *, replace: bool = False) -> None:
    """Register a factory in-process, bypassing entry points.

    For tests, notebooks, and applications that build backends in Python rather
    than through installed distributions. Takes precedence over entry points, so
    a test can shadow ``"procrastinate"`` with a fake without touching the
    installed package.
    """
    if name in _manual and not replace:
        raise ConfigurationError(f"backend factory {name!r} is already registered")
    _manual[name] = factory


def unregister_backend(name: str) -> None:
    """Remove a manually registered factory. Silent when it was not registered."""
    _manual.pop(name, None)


def registered_backends() -> Mapping[str, BackendFactory]:
    """The manually registered factories, for introspection and test teardown."""
    return dict(_manual)


@cache
def _entry_points() -> Mapping[str, Any]:
    from importlib.metadata import entry_points

    return {ep.name: ep for ep in entry_points(group=ENTRY_POINT_GROUP)}


def available_backends() -> tuple[str, ...]:
    """Every backend name this process could build, from all three sources."""
    return tuple(sorted({*DEFAULT_BACKENDS, *_entry_points(), *_manual}))


def resolve_factory(spec: str) -> BackendFactory:
    """Resolve a factory from a plugin name, a built-in alias, or an import string.

    Resolution order, most specific first:

    1. a factory registered with :func:`register_backend`;
    2. an installed ``taskferry.backends`` entry point;
    3. a built-in alias (``"inline"``, ``"thread"``, ``"process"``, ``"subprocess"``);
    4. an explicit ``"module.path:callable"`` import string.

    Raises:
        ConfigurationError: when nothing matches, listing what is available so
            the fix is usually visible in the error itself.
    """
    manual = _manual.get(spec)
    if manual is not None:
        return manual

    entry_point = _entry_points().get(spec)
    if entry_point is not None:
        try:
            loaded = entry_point.load()
        except Exception as exc:
            raise ConfigurationError(
                f"the {spec!r} backend plugin failed to load: {exc}. "
                f"Is its distribution installed correctly?"
            ) from exc
        return _as_factory(loaded, spec)

    builtin = DEFAULT_BACKENDS.get(spec)
    if builtin is not None:
        return _import_factory(builtin)

    if ":" in spec or "." in spec:
        return _import_factory(spec)

    known = ", ".join(available_backends())
    raise ConfigurationError(
        f"unknown backend {spec!r}; install the adapter that provides it, register it with "
        f"taskferry.register_backend(), or use a 'module:callable' import string "
        f"(available: {known})"
    )


def _import_factory(spec: str) -> BackendFactory:
    module_path, sep, attr = spec.partition(":")
    if not sep:
        module_path, _, attr = spec.rpartition(".")
    if not module_path or not attr:
        raise ConfigurationError(f"invalid factory {spec!r}; expected 'package.module:callable'")
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ConfigurationError(
            f"cannot import {module_path!r} for backend factory {spec!r}: {exc}. "
            f"The adapter distribution providing it is probably not installed."
        ) from exc
    try:
        target = getattr(module, attr)
    except AttributeError as exc:
        raise ConfigurationError(f"{module_path!r} has no attribute {attr!r}") from exc
    return _as_factory(target, spec)


def _as_factory(target: object, spec: str) -> BackendFactory:
    if not callable(target):
        raise ConfigurationError(f"backend factory {spec!r} is not callable")
    return cast(BackendFactory, target)


def build_backend(factory_spec: str, options: Mapping[str, Any]) -> ExecutionBackend:
    """Resolve ``factory_spec`` and call it with ``options`` as keyword arguments.

    A ``TypeError`` from the factory is translated into a
    :class:`~taskferry.core.errors.ConfigurationError`, because "you passed an
    option this backend does not accept" is a configuration problem and should
    read like one.
    """
    factory = resolve_factory(factory_spec)
    try:
        return factory(**dict(options))
    except TypeError as exc:
        raise ConfigurationError(
            f"backend {factory_spec!r} rejected its options {sorted(options)}: {exc}"
        ) from exc


__all__ = [
    "ENTRY_POINT_GROUP",
    "BackendFactory",
    "available_backends",
    "build_backend",
    "register_backend",
    "registered_backends",
    "resolve_factory",
    "unregister_backend",
]
