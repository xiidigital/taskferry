"""The Router — which backend runs this spec.

The whole point of Taskferry is that application code never names a provider. It
says *what* kind of work this is (`queue="metadata"`, `profile="gpu"`) and the
deployment decides *where* that runs:

```mermaid
flowchart TD
    SPEC["ExecutionSpec"]
    ROUTER["Router"]

    SPEC --> ROUTER

    ROUTER -->|"kind=task · queue=metadata"| PRO["procrastinate"]
    ROUTER -->|"kind=task · queue=http"| CT["cloudtasks"]
    ROUTER -->|"kind=job · profile=heavy"| CR["cloudrun"]
    ROUTER -->|"kind=job · profile=gpu"| K8S["kubernetes-gpu"]
    ROUTER -->|"kind=inline"| LOCAL["inline"]
```

Rules are ordered and explicit — **first match wins**, no scoring, no implicit
precedence to reason about. When nothing matches, the default backend for the
spec's kind is used; when there is no default either, routing raises
:class:`~taskferry.errors.RoutingError` naming what it tried, because silently
falling back to "whatever is around" is how work ends up on the wrong engine.

Routing is pure: same spec plus same rules, same answer. That makes it trivially
testable without a single backend instance.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from .errors import RoutingError
from .execution import ExecutionKind
from .specs import ExecutionSpec


@dataclass(frozen=True, slots=True)
class Route:
    """One ordered routing rule: match on portable attributes, pick a backend.

    Attributes:
        backend: Name of the backend to use when this rule matches.
        kind: Restrict to one execution kind. ``None`` matches any kind.
        queue: Match ``spec.queue``. Supports ``fnmatch`` globs (``"media-*"``).
        profile: Match ``spec.profile``. Supports globs.
        name: Match ``spec.name`` (task path or job name). Supports globs.
        labels: Every entry must be present with the same value in
            ``spec.labels``. Extra labels on the spec are ignored.

    Unset criteria simply do not constrain, so ``Route(backend="x")`` matches
    everything and is a legitimate catch-all at the end of a rule list.
    """

    backend: str
    kind: ExecutionKind | None = None
    queue: str | None = None
    profile: str | None = None
    name: str | None = None
    labels: Mapping[str, str] = field(default_factory=dict)

    def matches(self, spec: ExecutionSpec) -> bool:
        """Whether this rule applies to ``spec``."""
        if self.kind is not None and spec.kind is not self.kind:
            return False
        if self.queue is not None and not fnmatch.fnmatchcase(spec.queue, self.queue):
            return False
        if self.profile is not None and not fnmatch.fnmatchcase(spec.profile, self.profile):
            return False
        if self.name is not None and not fnmatch.fnmatchcase(spec.name, self.name):
            return False
        return all(spec.labels.get(key) == value for key, value in self.labels.items())

    def describe(self) -> str:
        """Human-readable form used by ``taskferry backends`` and error messages."""
        criteria = [
            f"{field_name}={value}"
            for field_name, value in (
                ("kind", self.kind.value if self.kind else None),
                ("queue", self.queue),
                ("profile", self.profile),
                ("name", self.name),
            )
            if value is not None
        ]
        criteria += [f"label:{k}={v}" for k, v in sorted(self.labels.items())]
        return f"{' '.join(criteria) or '*'} -> {self.backend}"


class Router:
    """Resolves a spec to a backend name. Immutable, pure, cheap to construct."""

    __slots__ = ("_defaults", "_routes")

    def __init__(
        self,
        routes: Iterable[Route] = (),
        *,
        defaults: Mapping[ExecutionKind, str] | None = None,
    ) -> None:
        self._routes: tuple[Route, ...] = tuple(routes)
        self._defaults: dict[ExecutionKind, str] = dict(defaults or {})

    @property
    def routes(self) -> Sequence[Route]:
        return self._routes

    @property
    def defaults(self) -> Mapping[ExecutionKind, str]:
        """Fallback backend per kind, used when no rule matches."""
        return dict(self._defaults)

    def __repr__(self) -> str:
        return f"Router(routes={len(self._routes)}, defaults={self._defaults})"

    def with_route(self, route: Route) -> Router:
        """Return a new router with ``route`` appended (lowest precedence)."""
        return Router((*self._routes, route), defaults=self._defaults)

    def with_default(self, kind: ExecutionKind, backend: str) -> Router:
        """Return a new router whose default for ``kind`` is ``backend``."""
        return Router(self._routes, defaults={**self._defaults, kind: backend})

    def resolve(self, spec: ExecutionSpec) -> str:
        """Return the backend name for ``spec``.

        Raises:
            RoutingError: when no rule matches and the kind has no default. The
                message lists the rules that were considered, so a
                misconfiguration is diagnosable from the traceback alone.
        """
        for route in self._routes:
            if route.matches(spec):
                return route.backend
        default = self._defaults.get(spec.kind)
        if default is not None:
            return default
        tried = "; ".join(route.describe() for route in self._routes) or "<no routes>"
        raise RoutingError(
            f"no backend for {spec.kind.value} spec {spec.name!r} "
            f"(queue={spec.queue!r}, profile={spec.profile!r}); "
            f"no default for kind {spec.kind.value!r}; routes tried: {tried}"
        )

    def explain(self, spec: ExecutionSpec) -> str:
        """Why ``spec`` routes where it does. For the CLI and for debugging."""
        for index, route in enumerate(self._routes):
            if route.matches(spec):
                return f"route #{index} ({route.describe()})"
        default = self._defaults.get(spec.kind)
        if default is not None:
            return f"default for kind {spec.kind.value} -> {default}"
        return "unroutable"


__all__ = ["Route", "Router"]
