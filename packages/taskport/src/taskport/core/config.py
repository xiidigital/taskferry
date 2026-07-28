"""Configuration primitives (sections 14-17, ADR-0006).

Principle: platform config / secrets / env flow into *application* settings,
which are then handed to Taskport. Taskport libraries must **not** reach into
``os.environ`` arbitrarily from deep inside adapters.

These helpers are therefore:

* **Opt-in** — an app may configure Taskport from Django settings, plain Python,
  a dict, ``.env``, Vault, Secret Manager, etc. Env vars are one option, never
  the only one.
* **Injectable** — env readers take the environment mapping as an argument
  (defaulting to ``os.environ``) so they are testable and never hidden.
* **Convention-friendly** — they read standard names (``REDIS_URL``,
  ``DATABASE_URL``, ``GOOGLE_CLOUD_PROJECT`` ...) rather than forcing a
  ``TASKPORT_`` prefix on variables the ecosystem already standardizes.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .errors import ConfigurationError
from .typing import JSONObject

_MISSING = object()

# Truthy/falsey spellings accepted by env_bool, matching common 12-factor usage.
_TRUE = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSE = frozenset({"0", "false", "f", "no", "n", "off", ""})


def env_str(
    key: str,
    default: str | None = None,
    *,
    required: bool = False,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Read a string from the environment mapping.

    Raises :class:`ConfigurationError` when ``required`` and absent, so missing
    configuration fails fast at startup (section 15).
    """
    source = environ if environ is not None else os.environ
    value = source.get(key)
    if value is None or value == "":
        if required:
            raise ConfigurationError(f"required environment variable {key!r} is not set")
        return default
    return value


def env_int(
    key: str,
    default: int | None = None,
    *,
    required: bool = False,
    environ: Mapping[str, str] | None = None,
) -> int | None:
    raw = env_str(key, None, required=required, environ=environ)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{key!r} must be an integer, got {raw!r}") from exc


def env_bool(
    key: str,
    default: bool = False,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    raw = env_str(key, None, environ=environ)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigurationError(f"{key!r} must be a boolean-like value, got {raw!r}")


def require[T](value: T | None, name: str) -> T:
    """Return ``value`` or raise a clear :class:`ConfigurationError` if ``None``."""
    if value is None:
        raise ConfigurationError(f"{name} is required but was not provided")
    return value


@dataclass(frozen=True, slots=True)
class ProviderOptions:
    """Provider-specific escape hatch (section 17).

    Portable config lives on the common models; anything genuinely
    provider-specific goes here, namespaced per provider::

        ProviderOptions({"gcp": {"http_target": {...}}, "aws": {"MessageGroupId": "x"}})

    Adapters read only their own namespace and ignore the rest, so one
    configuration object travels unchanged across providers.
    """

    by_provider: Mapping[str, JSONObject] = field(default_factory=dict)

    def __post_init__(self) -> None:
        frozen = {k: MappingProxyType(dict(v)) for k, v in self.by_provider.items()}
        object.__setattr__(self, "by_provider", MappingProxyType(frozen))

    def for_provider(self, provider: str) -> JSONObject:
        """Return this provider's options (an empty mapping if none supplied)."""
        return dict(self.by_provider.get(provider, {}))

    def option(
        self,
        provider: str,
        key: str,
        default: object = _MISSING,
    ) -> object:
        """Read a single option, raising if absent and no default is given."""
        opts = self.by_provider.get(provider, {})
        if key in opts:
            return opts[key]
        if default is _MISSING:
            raise ConfigurationError(
                f"provider option {provider!r}.{key!r} is required but missing"
            )
        return default


def resolve_factory(spec: str) -> Callable[..., object]:
    """Resolve a ``"module.path:attr"`` (or ``"module.path.attr"``) string to a callable.

    Used by registries (ADR and section 35) to build providers lazily from
    configuration without importing every adapter eagerly.
    """
    import importlib

    module_path, sep, attr = spec.partition(":")
    if not sep:
        module_path, _, attr = spec.rpartition(".")
    if not module_path or not attr:
        raise ConfigurationError(f"invalid factory spec {spec!r}")
    try:
        module = importlib.import_module(module_path)
        target = getattr(module, attr)
    except (ImportError, AttributeError) as exc:
        raise ConfigurationError(f"could not import factory {spec!r}: {exc}") from exc
    if not callable(target):
        raise ConfigurationError(f"factory {spec!r} is not callable")
    return target  # type: ignore[no-any-return]


__all__ = [
    "ProviderOptions",
    "env_bool",
    "env_int",
    "env_str",
    "require",
    "resolve_factory",
]
