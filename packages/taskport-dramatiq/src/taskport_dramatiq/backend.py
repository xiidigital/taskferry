"""The Dramatiq `TaskBackend`.

Capabilities, next to Procrastinate's
-------------------------------------

The two worker-based engines Taskport adapts, side by side — and the differences
are exactly what the capability model exists to state rather than smooth over:

| | Procrastinate | Dramatiq |
| --- | :---: | :---: |
| `SUBMIT` | yes | yes |
| `DELAY` | yes | yes (``delay`` in ms) |
| `RETRY` | yes | yes (native backoff + jitter) |
| `PRIORITY` | yes | **no** — Dramatiq has no message priority |
| `DEDUPLICATION` | yes (``queueing_lock``) | **no** — needs a middleware you own |
| `STATE` | yes (job manager) | **no** — no per-message lookup |
| `CANCEL` | yes | **no** |
| `RESULT` | **no** | **no** |

`STATE` and `CANCEL` are the interesting absences. Dramatiq's default brokers keep
no queryable record of an individual message, so there is nothing to look up and
nothing to revoke. A handle from this backend refuses `status()` rather than
returning a plausible `UNKNOWN` forever.

Dramatiq *does* have a results backend as an optional middleware, and an
application that configures one can read results through Dramatiq directly. This
adapter does not advertise `RESULT`, because whether it works depends on
middleware Taskport cannot see from here — and a capability that is true only
sometimes is worse than one that is honestly absent.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from typing import Any

from taskport.capabilities import Capability, CapabilitySet
from taskport.core.provider import ProviderMetadata
from taskport.envelope import build_envelope
from taskport.errors import ConfigurationError, SubmissionError
from taskport.execution import Execution, ExecutionKind, ExecutionState, new_execution_id
from taskport.ports import BaseBackend
from taskport.specs import ExecutionSpec, TaskSpec

from .worker import DEFAULT_ACTOR_NAME

DRAMATIQ_CAPABILITIES = frozenset(
    {
        Capability.SUBMIT,
        Capability.DELAY,
        Capability.RETRY,
    }
)


class DramatiqTaskBackend(BaseBackend):
    """Sends Taskport tasks as Dramatiq messages.

    Args:
        actor: The dispatch actor built by
            :func:`~taskport_dramatiq.build_dispatch_actor`, or a
            ``"module:attribute"`` string resolved on first use. A string keeps
            this backend constructible in a web process that has not connected to
            the broker yet.
        actor_name: Name to look the actor up by when a broker is given instead.
        broker: A Dramatiq broker, as an alternative to passing the actor —
            the actor is then fetched from the broker's registry.

    Backend options, under the ``"dramatiq"`` namespace: any keyword
    ``send_with_options`` accepts (``max_retries``, ``time_limit``, ``pipe_ignore``).
    """

    def __init__(
        self,
        *,
        actor: Any = None,
        actor_name: str = DEFAULT_ACTOR_NAME,
        broker: Any = None,
        name: str = "dramatiq",
    ) -> None:
        self._actor = actor
        self._actor_name = actor_name
        self._broker = broker
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def kind(self) -> ExecutionKind:
        return ExecutionKind.TASK

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(DRAMATIQ_CAPABILITIES, provider=self._name)

    def actor(self) -> Any:
        """The dispatch actor, resolving a dotted path or a broker lookup once."""
        if self._actor is None and self._broker is not None:
            actors = getattr(self._broker, "actors", {})
            resolved = actors.get(self._actor_name)
            if resolved is None:
                raise ConfigurationError(
                    f"the broker has no actor named {self._actor_name!r}; call "
                    f"build_dispatch_actor() in the module your worker loads"
                )
            self._actor = resolved
        if self._actor is None:
            raise ConfigurationError(
                f"{self._name!r} needs a dispatch actor: pass actor=<actor>, "
                "actor='myapp.worker:taskport_execute', or broker=<broker>. "
                "Build it with taskport_dramatiq.build_dispatch_actor()."
            )
        if isinstance(self._actor, str):
            self._actor = _import_attribute(self._actor)
        return self._actor

    # -- submission --------------------------------------------------------------- #
    def _delay_ms(self, spec: TaskSpec) -> int | None:
        """Translate ``delay``/``run_at`` into Dramatiq's milliseconds."""
        scheduled = spec.scheduled_for()
        if scheduled is None:
            return None
        seconds = (scheduled - datetime.now(UTC)).total_seconds()
        return max(0, int(seconds * 1000))

    def _submit(self, spec: ExecutionSpec) -> Execution:
        assert isinstance(spec, TaskSpec)
        # Resolve first, outside the try below: a missing actor is a
        # ConfigurationError and must stay one, not be reported as "the broker
        # refused the message".
        actor = self.actor()
        options = spec.options_for("dramatiq")

        send_options: dict[str, Any] = {
            "args": (build_envelope(spec),),
            "queue_name": spec.queue,
            **options,
        }
        delay = self._delay_ms(spec)
        if delay is not None:
            send_options["delay"] = delay
        if spec.retry.enabled:
            # Dramatiq counts retries; RetryPolicy counts total attempts.
            send_options.setdefault("max_retries", spec.retry.engine_attempts - 1)

        try:
            sent = actor.send_with_options(**send_options)
        except Exception as exc:
            raise SubmissionError(
                f"Dramatiq could not send {spec.task!r} to queue {spec.queue!r}: {exc}",
                backend=self._name,
            ) from exc

        message_id = getattr(sent, "message_id", None)
        return Execution(
            id=new_execution_id(ExecutionKind.TASK),
            kind=ExecutionKind.TASK,
            backend=self._name,
            # The last thing this backend can honestly observe.
            state=ExecutionState.QUEUED,
            name=spec.name,
            created_at=datetime.now(UTC),
            external_id=str(message_id) if message_id else None,
            correlation=spec.correlation,
            provider_metadata=ProviderMetadata(
                provider="dramatiq",
                provider_id=str(message_id) if message_id else None,
                resource=f"queue:{spec.queue}",
                labels=dict(spec.labels),
            ),
            metadata={"queue": spec.queue, "actor": self._actor_name},
        )


def _import_attribute(path: str) -> Any:
    module_path, sep, attr = path.partition(":")
    if not sep:
        module_path, _, attr = path.rpartition(".")
    if not module_path or not attr:
        raise ConfigurationError(f"invalid actor reference {path!r}; expected 'module:attribute'")
    try:
        return getattr(importlib.import_module(module_path), attr)
    except (ImportError, AttributeError) as exc:
        raise ConfigurationError(f"cannot resolve Dramatiq actor {path!r}: {exc}") from exc


def make_backend(**options: Any) -> DramatiqTaskBackend:
    """Entry point for ``{"factory": "dramatiq", ...}`` configuration."""
    return DramatiqTaskBackend(
        actor=options.get("actor"),
        actor_name=str(options.get("actor_name", DEFAULT_ACTOR_NAME)),
        broker=options.get("broker"),
        name=str(options.get("name", "dramatiq")),
    )


__all__ = [
    "DEFAULT_ACTOR_NAME",
    "DRAMATIQ_CAPABILITIES",
    "DramatiqTaskBackend",
    "make_backend",
]
