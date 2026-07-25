"""A tiny lazy registry used by every domain (sections 35, 31).

``runners["default"]``, ``publishers["default"]``, ``schedulers["default"]`` are
all backed by this. Providers are built **lazily** on first access and cached, so
``import taskport.jobs`` never imports ``boto3``/``google.cloud`` for an adapter
you don't touch.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping

from .config import resolve_factory
from .errors import ConfigurationError


class LazyRegistry[T]:
    """Maps an alias to a zero-arg factory, instantiating on first access.

    ``kind`` is a human label used in error messages (e.g. ``"job runner"``).
    """

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._factories: dict[str, Callable[[], T]] = {}
        self._instances: dict[str, T] = {}

    def register(self, alias: str, factory: Callable[[], T], *, replace: bool = False) -> None:
        """Register a zero-arg ``factory`` under ``alias``."""
        if alias in self._factories and not replace:
            raise ConfigurationError(f"{self._kind} alias {alias!r} is already registered")
        self._factories[alias] = factory
        self._instances.pop(alias, None)

    def register_spec(self, alias: str, spec: str, /, **kwargs: object) -> None:
        """Register by import string, resolved lazily on first access.

        ``spec`` is ``"module.path:Factory"``; ``kwargs`` are passed to it.
        """

        def _factory() -> T:
            target = resolve_factory(spec)
            return target(**kwargs)  # type: ignore[return-value]

        self.register(alias, _factory, replace=True)

    def configure(self, config: Mapping[str, Mapping[str, object]]) -> None:
        """Bulk-register from a settings-style mapping.

        Each entry maps an alias to ``{"factory": "mod:Cls", ...kwargs}``. This is
        the shape ``taskport.jobs.runners.configure(settings.JOBS)`` consumes.
        """
        for alias, entry in config.items():
            params = dict(entry)
            factory_spec = params.pop("factory", None)
            if not isinstance(factory_spec, str):
                raise ConfigurationError(
                    f"{self._kind} {alias!r} config needs a 'factory' import string"
                )
            self.register_spec(alias, factory_spec, **params)

    def __getitem__(self, alias: str) -> T:
        if alias not in self._instances:
            factory = self._factories.get(alias)
            if factory is None:
                known = ", ".join(sorted(self._factories)) or "<none>"
                raise ConfigurationError(
                    f"no {self._kind} registered under {alias!r} (known: {known})"
                )
            self._instances[alias] = factory()
        return self._instances[alias]

    def __contains__(self, alias: object) -> bool:
        return alias in self._factories

    def __iter__(self) -> Iterator[str]:
        return iter(self._factories)

    def aliases(self) -> tuple[str, ...]:
        return tuple(self._factories)

    def clear(self) -> None:
        """Drop all registrations and cached instances (useful in tests)."""
        self._factories.clear()
        self._instances.clear()

    def reset_instances(self) -> None:
        """Drop cached instances but keep registrations (re-build on next access)."""
        self._instances.clear()


__all__ = ["LazyRegistry"]
