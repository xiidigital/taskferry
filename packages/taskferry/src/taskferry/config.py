"""Typed configuration — one shape, several sources.

```mermaid
flowchart TD
    ENV["Environment<br/>TASKFERRY_*"]
    PY["Python<br/>TaskferryConfig(...)"]
    MAP["Mapping<br/>from_mapping(...)"]
    DJ["Django settings<br/>(taskferry_django)"]
    FILE["TOML / YAML<br/>(application's job)"]

    CONFIG["TaskferryConfig"]
    RUNTIME["Taskferry runtime"]

    ENV --> CONFIG
    PY --> CONFIG
    MAP --> CONFIG
    DJ --> CONFIG
    FILE --> CONFIG
    CONFIG --> RUNTIME
```

Environment variables are an important *source* — they are how a Twelve-Factor
deployment configures anything — but they are never the internal model. The
internal model is :class:`TaskferryConfig`: typed, immutable, validated once at
construction, and inspectable. Everything else is a loader that produces one.

The core never reads ``django.conf.settings``, a config file path, or a
framework's registry. ``taskferry_django.config_from_settings()`` builds a
``TaskferryConfig`` from ``settings.TASKFERRY`` and hands it over — the dependency
points inward, always.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from .core.errors import ConfigurationError
from .execution import ExecutionKind
from .router import Route, Router

ENV_PREFIX = "TASKFERRY_"

DEFAULT_BACKENDS: Mapping[str, str] = MappingProxyType(
    {
        "inline": "taskferry.backends.inline:InlineExecutionBackend",
        "thread": "taskferry.backends.thread:ThreadTaskBackend",
        "process": "taskferry.backends.process:ProcessTaskBackend",
        "subprocess": "taskferry.backends.subprocess:SubprocessJobBackend",
    }
)
"""The built-in backends, always available without any plugin installed."""


@dataclass(frozen=True, slots=True)
class BackendConfig:
    """How to build one backend.

    Attributes:
        factory: Either a plugin name registered under the ``taskferry.backends``
            entry-point group (``"procrastinate"``, ``"cloudrun"``), or an
            explicit ``"module.path:callable"`` import string. Names are tried as
            plugins first, then as the built-in aliases.
        options: Keyword arguments handed to the factory. Provider-specific by
            nature — this is the one place provider vocabulary is expected.
    """

    factory: str
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.factory:
            raise ConfigurationError("BackendConfig.factory must not be empty")
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> BackendConfig:
        payload = dict(data)
        factory = payload.pop("factory", None) or payload.pop("backend", None)
        if not isinstance(factory, str):
            raise ConfigurationError(
                "backend configuration needs a 'factory' (plugin name or 'module:callable')"
            )
        options = payload.pop("options", None)
        if options is None:
            options = payload  # allow flat form: {"factory": ..., "project": ...}
        elif payload:
            raise ConfigurationError(
                f"backend configuration mixes 'options' with extra keys: {sorted(payload)}"
            )
        if not isinstance(options, Mapping):
            raise ConfigurationError("backend 'options' must be a mapping")
        return cls(factory=factory, options=dict(options))


@dataclass(frozen=True, slots=True)
class TaskferryConfig:
    """Everything the runtime needs, resolved and validated.

    Attributes:
        backends: Named backend definitions. Instantiated lazily, so configuring
            a Cloud Run backend costs nothing until a job is actually routed to
            it, and importing the Google SDK never happens in a web process that
            only enqueues tasks.
        routes: Ordered routing rules. See :class:`~taskferry.router.Route`.
        defaults: Fallback backend per execution kind.
        allow_import: Whether unregistered task names may be imported dynamically.
        allowed_modules: Import allowlist for task names. Empty means "any
            module" and is only appropriate when task names are trusted.
        max_tracked_executions: How many ``id -> backend`` pairs the runtime
            remembers so ``runtime.get(id)`` can find the right backend without
            being told. Bounded on purpose — a long-lived producer must not grow
            a map forever.
    """

    backends: Mapping[str, BackendConfig] = field(default_factory=dict)
    routes: Sequence[Route] = ()
    defaults: Mapping[ExecutionKind, str] = field(default_factory=dict)
    allow_import: bool = True
    allowed_modules: Sequence[str] = ()
    max_tracked_executions: int = 10_000

    def __post_init__(self) -> None:
        object.__setattr__(self, "backends", MappingProxyType(dict(self.backends)))
        object.__setattr__(self, "routes", tuple(self.routes))
        object.__setattr__(self, "defaults", MappingProxyType(dict(self.defaults)))
        object.__setattr__(self, "allowed_modules", tuple(self.allowed_modules))
        if self.max_tracked_executions < 0:
            raise ConfigurationError("max_tracked_executions must be >= 0")
        self._validate_references()

    def _validate_references(self) -> None:
        """Fail at construction when a route names a backend that is not defined.

        Catching this here means a typo in a route surfaces at startup rather
        than the first time that queue happens to be used in production.
        """
        known = set(self.backends)
        for route in self.routes:
            if route.backend not in known:
                raise ConfigurationError(
                    f"route {route.describe()!r} names unknown backend {route.backend!r} "
                    f"(defined: {', '.join(sorted(known)) or '<none>'})"
                )
        for kind, backend in self.defaults.items():
            if backend not in known:
                raise ConfigurationError(
                    f"default backend for {kind.value!r} is {backend!r}, which is not defined "
                    f"(defined: {', '.join(sorted(known)) or '<none>'})"
                )

    # -- derived ------------------------------------------------------------ #
    def router(self) -> Router:
        """Build the :class:`~taskferry.router.Router` this configuration describes."""
        return Router(self.routes, defaults=self.defaults)

    def evolve(self, **changes: Any) -> TaskferryConfig:
        """Return a modified copy. Configs are immutable."""
        return replace(self, **changes)

    def with_backend(self, name: str, config: BackendConfig) -> TaskferryConfig:
        """Return a copy with one more backend defined."""
        return self.evolve(backends={**self.backends, name: config})

    # -- loaders ------------------------------------------------------------ #
    @classmethod
    def local(cls) -> TaskferryConfig:
        """The zero-infrastructure configuration.

        Inline runs in this process, tasks run on a thread pool, jobs run as
        subprocesses. No queue, no database, no cloud, no worker. This is what
        :meth:`taskferry.Taskferry.local` uses, and it is a genuinely useful
        production configuration for a CLI or a single-process tool.
        """
        return cls(
            backends={
                "inline": BackendConfig(factory="inline"),
                "thread": BackendConfig(factory="thread"),
                "subprocess": BackendConfig(factory="subprocess"),
            },
            defaults={
                ExecutionKind.INLINE: "inline",
                ExecutionKind.TASK: "thread",
                ExecutionKind.JOB: "subprocess",
            },
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> TaskferryConfig:
        """Build from a plain mapping — the shape a TOML/YAML/settings loader produces.

        ::

            {
              "backends": {
                "metadata": {"factory": "procrastinate", "app": "myapp.tasks:app"},
                "heavy":    {"factory": "cloudrun", "project": "p", "location": "eu"},
              },
              "routes": [
                {"kind": "task", "queue": "metadata", "backend": "metadata"},
                {"kind": "job",  "profile": "heavy",  "backend": "heavy"},
              ],
              "defaults": {"inline": "inline", "task": "metadata", "job": "heavy"},
            }
        """
        backends_raw = data.get("backends") or {}
        if not isinstance(backends_raw, Mapping):
            raise ConfigurationError("'backends' must be a mapping of name -> configuration")
        backends = {
            str(name): BackendConfig.from_mapping(entry)
            if isinstance(entry, Mapping)
            else BackendConfig(factory=str(entry))
            for name, entry in backends_raw.items()
        }

        routes_raw = data.get("routes") or ()
        if isinstance(routes_raw, Mapping):
            raise ConfigurationError("'routes' must be an ordered sequence, not a mapping")
        routes = tuple(_route_from_mapping(entry) for entry in routes_raw)

        defaults_raw = data.get("defaults") or {}
        if not isinstance(defaults_raw, Mapping):
            raise ConfigurationError("'defaults' must be a mapping of kind -> backend name")
        defaults = {_parse_kind(key): str(value) for key, value in defaults_raw.items()}

        return cls(
            backends=backends,
            routes=routes,
            defaults=defaults,
            allow_import=bool(data.get("allow_import", True)),
            allowed_modules=tuple(str(m) for m in (data.get("allowed_modules") or ())),
            max_tracked_executions=int(data.get("max_tracked_executions", 10_000)),
        )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> TaskferryConfig:
        """Build from ``TASKFERRY_*`` environment variables.

        Two variables carry the structure, both JSON so that nested provider
        options survive intact:

        ``TASKFERRY_BACKENDS``
            ``{"metadata": {"factory": "procrastinate", "app": "myapp:app"}}``

        ``TASKFERRY_ROUTES``
            ``[{"kind": "task", "queue": "metadata", "backend": "metadata"}]``

        And the scalars:

        ``TASKFERRY_DEFAULT_INLINE`` · ``TASKFERRY_DEFAULT_TASK`` ·
        ``TASKFERRY_DEFAULT_JOB`` · ``TASKFERRY_ALLOW_IMPORT`` ·
        ``TASKFERRY_ALLOWED_MODULES`` (comma-separated).

        With nothing set at all this returns :meth:`local`, so a container that
        forgot to configure Taskferry still starts and still runs work — locally,
        visibly, and without pretending to have reached a queue.
        """
        source = environ if environ is not None else os.environ
        raw_backends = source.get(f"{ENV_PREFIX}BACKENDS")
        raw_routes = source.get(f"{ENV_PREFIX}ROUTES")

        payload: dict[str, Any] = {}
        if raw_backends:
            payload["backends"] = _json_env(f"{ENV_PREFIX}BACKENDS", raw_backends, Mapping)
        if raw_routes:
            payload["routes"] = _json_env(f"{ENV_PREFIX}ROUTES", raw_routes, list)

        defaults = {
            kind.value: source[f"{ENV_PREFIX}DEFAULT_{kind.value.upper()}"]
            for kind in ExecutionKind
            if source.get(f"{ENV_PREFIX}DEFAULT_{kind.value.upper()}")
        }
        if defaults:
            payload["defaults"] = defaults

        if not payload:
            return cls.local()

        allow_import = source.get(f"{ENV_PREFIX}ALLOW_IMPORT")
        if allow_import is not None:
            falsey = {"0", "false", "no", "off"}
            payload["allow_import"] = allow_import.strip().lower() not in falsey
        allowed = source.get(f"{ENV_PREFIX}ALLOWED_MODULES")
        if allowed:
            payload["allowed_modules"] = [m.strip() for m in allowed.split(",") if m.strip()]
        tracked = source.get(f"{ENV_PREFIX}MAX_TRACKED_EXECUTIONS")
        if tracked:
            payload["max_tracked_executions"] = _int_env(
                f"{ENV_PREFIX}MAX_TRACKED_EXECUTIONS", tracked
            )
        return cls.from_mapping(payload)


def _json_env(key: str, raw: str, expected: type) -> Any:
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{key} must contain valid JSON: {exc}") from exc
    if not isinstance(value, expected):
        raise ConfigurationError(f"{key} must be a JSON {expected.__name__.lower()}")
    return value


def _int_env(key: str, raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{key} must be an integer, got {raw!r}") from exc


def _parse_kind(value: object) -> ExecutionKind:
    if isinstance(value, ExecutionKind):
        return value
    try:
        return ExecutionKind(str(value))
    except ValueError as exc:
        known = ", ".join(k.value for k in ExecutionKind)
        raise ConfigurationError(f"unknown execution kind {value!r} (known: {known})") from exc


def _route_from_mapping(entry: object) -> Route:
    if isinstance(entry, Route):
        return entry
    if not isinstance(entry, Mapping):
        raise ConfigurationError(f"route entries must be mappings, got {type(entry).__name__}")
    data = dict(entry)
    backend = data.pop("backend", None)
    if not isinstance(backend, str):
        raise ConfigurationError("each route needs a 'backend' name")
    kind = data.pop("kind", None)
    labels = data.pop("labels", None) or {}
    if not isinstance(labels, Mapping):
        raise ConfigurationError("route 'labels' must be a mapping")
    unknown = set(data) - {"queue", "profile", "name"}
    if unknown:
        raise ConfigurationError(f"unknown route keys: {', '.join(sorted(unknown))}")
    return Route(
        backend=backend,
        kind=_parse_kind(kind) if kind is not None else None,
        queue=_opt_str(data.get("queue")),
        profile=_opt_str(data.get("profile")),
        name=_opt_str(data.get("name")),
        labels={str(k): str(v) for k, v in labels.items()},
    )


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)


__all__ = [
    "DEFAULT_BACKENDS",
    "ENV_PREFIX",
    "BackendConfig",
    "TaskferryConfig",
]
