"""The Azure Service Bus `TaskBackend`.

Capabilities
------------

Advertised: ``SUBMIT``, ``DELAY``, ``RETRY``, ``DEDUPLICATION``.

``DELAY`` is unrestricted here, unlike SQS: ``scheduled_enqueue_time_utc`` takes
an absolute time and Service Bus honours it however far out it is. ``retry`` maps
onto the queue's ``MaxDeliveryCount`` and dead-letter behaviour, which the queue
itself is configured with.

``DEDUPLICATION`` is real but conditional: Service Bus deduplicates on
``message_id`` only when the queue has *duplicate detection* enabled, within its
configured history window. The capability is advertised because the mechanism
exists and the adapter uses it; the window is the queue's setting, and the
docstring says so rather than implying a guarantee. Taskferry never promises
exactly-once — see [ADR-0010](../../../docs/adr/0010-delivery-semantics.md).

Not advertised: ``STATE``, ``RESULT``, ``CANCEL``. Service Bus offers no lookup of
one message by id after it is enqueued.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from taskferry.capabilities import Capability, CapabilitySet
from taskferry.core.ids import new_id
from taskferry.core.provider import ProviderMetadata
from taskferry.envelope import build_envelope
from taskferry.errors import ConfigurationError, SubmissionError
from taskferry.execution import Execution, ExecutionKind, ExecutionState, new_execution_id
from taskferry.ports import BaseBackend
from taskferry.specs import ExecutionSpec, TaskSpec

SERVICE_BUS_CAPABILITIES = frozenset(
    {
        Capability.SUBMIT,
        Capability.DELAY,
        Capability.RETRY,
        Capability.DEDUPLICATION,
    }
)

MessageFactory = Callable[..., Any]


class ServiceBusTaskBackend(BaseBackend):
    """Sends Taskferry tasks to an Azure Service Bus queue.

    Args:
        connection_string: Service Bus connection string. Not needed when a
            ``client`` is injected.
        queue_name: Target queue.
        client: Injected ``ServiceBusClient`` for testing.
        credential: Injected credential; ``DefaultAzureCredential`` is used with
            ``namespace`` when no connection string is given, which is how a
            Managed Identity deployment authenticates.
        namespace: Fully-qualified namespace (``myns.servicebus.windows.net``),
            for credential-based authentication.
        message_factory: Injected ``ServiceBusMessage`` constructor, for tests.

    Backend options, under the ``"servicebus"`` namespace: ``session_id``,
    ``application_properties``, ``time_to_live``.
    """

    def __init__(
        self,
        *,
        connection_string: str | None = None,
        queue_name: str | None = None,
        client: Any = None,
        credential: Any = None,
        namespace: str | None = None,
        message_factory: MessageFactory | None = None,
        name: str = "servicebus",
    ) -> None:
        if not queue_name:
            raise ConfigurationError("ServiceBusTaskBackend needs 'queue_name'")
        if client is None and not connection_string and not (credential and namespace):
            raise ConfigurationError(
                "ServiceBusTaskBackend needs either 'connection_string', or "
                "'namespace' plus a 'credential' (Managed Identity), or an injected client"
            )
        self._connection_string = connection_string
        self._queue_name = queue_name
        self._client = client
        self._credential = credential
        self._namespace = namespace
        self._message_factory = message_factory
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def kind(self) -> ExecutionKind:
        return ExecutionKind.TASK

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(SERVICE_BUS_CAPABILITIES, provider=self._name)

    @property
    def queue_name(self) -> str:
        return self._queue_name

    def _service_bus(self) -> Any:
        if self._client is None:
            try:
                from azure.servicebus import ServiceBusClient
            except ImportError as exc:  # pragma: no cover - depends on the environment
                raise ConfigurationError(
                    "the Service Bus backend needs the Azure SDK: "
                    "pip install 'taskferry-servicebus[azure]'"
                ) from exc
            if self._connection_string:
                self._client = ServiceBusClient.from_connection_string(self._connection_string)
            else:
                self._client = ServiceBusClient(
                    fully_qualified_namespace=self._namespace, credential=self._credential
                )
        return self._client

    def _build_message(self, spec: TaskSpec, message_id: str) -> Any:
        options = spec.options_for("servicebus")
        properties: dict[str, Any] = {
            "taskferry-task": spec.task,
            "taskferry-queue": spec.queue,
            **dict(options.get("application_properties") or {}),  # type: ignore[arg-type]
        }
        kwargs: dict[str, Any] = {
            "message_id": message_id,
            "application_properties": properties,
            "scheduled_enqueue_time_utc": spec.scheduled_for(),
        }
        if options.get("session_id"):
            kwargs["session_id"] = str(options["session_id"])
        if options.get("time_to_live"):
            kwargs["time_to_live"] = options["time_to_live"]

        body = json.dumps(build_envelope(spec))
        if self._message_factory is not None:
            return self._message_factory(body, **kwargs)

        from azure.servicebus import ServiceBusMessage

        return ServiceBusMessage(body, **kwargs)

    # -- submission ------------------------------------------------------------ #
    def _submit(self, spec: ExecutionSpec) -> Execution:
        assert isinstance(spec, TaskSpec)
        # The message id is what Service Bus deduplicates on, so an idempotency
        # key must become it — otherwise the key would be decorative.
        message_id = spec.idempotency_key or new_id("sbmsg")
        message = self._build_message(spec, message_id)

        try:
            sender = self._service_bus().get_queue_sender(self._queue_name)
            sender.send_messages(message)
        except Exception as exc:
            raise SubmissionError(
                f"Service Bus could not send {spec.task!r} to queue {self._queue_name!r}: {exc}",
                backend=self._name,
            ) from exc

        return Execution(
            id=new_execution_id(ExecutionKind.TASK),
            kind=ExecutionKind.TASK,
            backend=self._name,
            state=ExecutionState.QUEUED,
            name=spec.name,
            created_at=datetime.now(UTC),
            external_id=message_id,
            correlation=spec.correlation,
            provider_metadata=ProviderMetadata(
                provider="azure",
                provider_id=message_id,
                resource=self._queue_name,
                labels=dict(spec.labels),
            ),
            metadata={"queue": spec.queue, "queue_name": self._queue_name},
        )


def make_backend(**options: Any) -> ServiceBusTaskBackend:
    """Entry point for ``{"factory": "servicebus", ...}`` configuration."""
    return ServiceBusTaskBackend(
        connection_string=options.get("connection_string"),
        queue_name=options.get("queue_name"),
        client=options.get("client"),
        credential=options.get("credential"),
        namespace=options.get("namespace"),
        message_factory=options.get("message_factory"),
        name=str(options.get("name", "servicebus")),
    )


__all__ = ["SERVICE_BUS_CAPABILITIES", "ServiceBusTaskBackend", "make_backend"]
