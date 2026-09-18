"""Schedule targets — *what* to fire.

A scheduler does not run business logic itself (section 12); it triggers another
capability. The portable primitives are:

* :class:`HttpTarget` — hit an HTTP endpoint (e.g. your Django task webhook, so a
  schedule fires a Task; or any service).
* :class:`PubSubTarget` — publish a message to a topic (so a schedule fires an
  Event, which may fan out to Tasks/Jobs).
* :class:`CallableTarget` — call a Python callable **in-process**. Local/dev only
  and intentionally non-portable; cloud adapters reject it.

This is composition by capability (Schedule → Task/Job/Event), not a workflow
engine (sections 45, 46).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from taskferry.core import JSONObject


@dataclass(frozen=True, slots=True)
class HttpTarget:
    """Fire an HTTP request. Portable across cloud schedulers."""

    url: str
    method: str = "POST"
    headers: Mapping[str, str] = field(default_factory=dict)
    body: str | None = None


@dataclass(frozen=True, slots=True)
class PubSubTarget:
    """Publish to a topic (GCP Pub/Sub-style). Portable to pub/sub-capable schedulers."""

    topic: str
    data: JSONObject = field(default_factory=dict)
    attributes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CallableTarget:
    """Call a Python callable in-process. Local/dev only — not portable."""

    func: Callable[..., object]
    args: tuple[object, ...] = ()
    kwargs: Mapping[str, object] = field(default_factory=dict)

    def fire(self) -> object:
        return self.func(*self.args, **dict(self.kwargs))


Target = HttpTarget | PubSubTarget | CallableTarget


__all__ = ["CallableTarget", "HttpTarget", "PubSubTarget", "Target"]
