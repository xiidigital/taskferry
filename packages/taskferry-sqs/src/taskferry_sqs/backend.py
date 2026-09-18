"""The SQS `TaskBackend`.

Capabilities, and the honest limits
-----------------------------------

Advertised: ``SUBMIT``, ``DELAY`` (standard queues only), ``RETRY`` (the queue's
redrive policy), ``DEDUPLICATION`` (FIFO queues only, via
``MessageDeduplicationId``).

Not advertised: ``STATE``, ``RESULT``, ``CANCEL``. Once a message is on a queue,
SQS offers no way to look up one message by id, no place a return value is kept,
and no way to delete a specific unread message. Pretending otherwise would mean
inventing a side table — a queue feature, and not this project's job.

The two limits that are real and are enforced rather than clamped:

**900 seconds of delay, maximum.** A spec asking for more is rejected with an
error naming the limit, instead of silently arriving 15 minutes from now when the
caller asked for tomorrow. Use a scheduler for longer delays.

**FIFO queues cannot delay individual messages.** On a `.fifo` queue,
``DelaySeconds`` is a queue-level setting, so a per-message delay is refused
rather than dropped.

That is why the capability set is computed **per queue** rather than being a
class constant: a FIFO queue and a standard queue genuinely differ, and one
`SQSTaskBackend` class serving both must tell the truth about which it is.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from taskferry.capabilities import Capability, CapabilitySet
from taskferry.core.provider import ProviderMetadata
from taskferry.envelope import build_envelope
from taskferry.errors import ConfigurationError, SubmissionError
from taskferry.execution import Execution, ExecutionKind, ExecutionState, new_execution_id
from taskferry.ports import BaseBackend
from taskferry.specs import ExecutionSpec, TaskSpec

MAX_DELAY_SECONDS = 900
"""SQS's hard ceiling on per-message delivery delay."""

#: What every SQS queue can do.
SQS_CAPABILITIES = frozenset(
    {
        Capability.SUBMIT,
        Capability.RETRY,
        Capability.DELAY,
    }
)

#: A FIFO queue trades per-message delay for ordering and deduplication.
FIFO_CAPABILITIES = frozenset(
    {
        Capability.SUBMIT,
        Capability.RETRY,
        Capability.DEDUPLICATION,
    }
)


class SQSTaskBackend(BaseBackend):
    """Sends Taskferry tasks to an AWS SQS queue.

    Args:
        queue_url: Full queue URL. A ``.fifo`` suffix switches the capability set.
        region: AWS region. ``None`` uses boto3's own resolution.
        client: Injected ``boto3.client("sqs")`` for testing.
        message_group_id: Default FIFO message group. Falls back to the spec's
            queue name, so ordering is per logical queue unless told otherwise.

    Backend options, under the ``"sqs"`` namespace: ``message_group_id``,
    ``message_attributes``, ``deduplication_id``.
    """

    def __init__(
        self,
        *,
        queue_url: str | None = None,
        region: str | None = None,
        client: Any = None,
        message_group_id: str | None = None,
        name: str = "sqs",
    ) -> None:
        if not queue_url:
            raise ConfigurationError(
                "SQSTaskBackend needs 'queue_url' "
                "(e.g. https://sqs.eu-west-1.amazonaws.com/123456789/tasks)"
            )
        self._queue_url = queue_url
        self._region = region
        self._client = client
        self._message_group_id = message_group_id
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def kind(self) -> ExecutionKind:
        return ExecutionKind.TASK

    @property
    def is_fifo(self) -> bool:
        return self._queue_url.endswith(".fifo")

    @property
    def capabilities(self) -> CapabilitySet:
        """Computed per queue: FIFO and standard queues genuinely differ."""
        return CapabilitySet(
            FIFO_CAPABILITIES if self.is_fifo else SQS_CAPABILITIES, provider=self._name
        )

    def _sqs(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - depends on the environment
                raise ConfigurationError(
                    "the SQS backend needs boto3: pip install 'taskferry-sqs[aws]'"
                ) from exc
            self._client = boto3.client("sqs", region_name=self._region)
        return self._client

    # -- submission --------------------------------------------------------------- #
    def _delay_seconds(self, spec: TaskSpec) -> int:
        """Translate ``delay``/``run_at`` into ``DelaySeconds``, or refuse."""
        scheduled = spec.scheduled_for()
        if scheduled is None:
            return 0
        delay = int((scheduled - datetime.now(UTC)).total_seconds())
        if delay <= 0:
            return 0
        if delay > MAX_DELAY_SECONDS:
            raise SubmissionError(
                f"SQS delivers a message at most {MAX_DELAY_SECONDS}s in the future; "
                f"{spec.task!r} asked for {delay}s. Use a scheduler to fire it when due, "
                f"or route this queue at an engine that schedules natively.",
                backend=self._name,
            )
        return delay

    def _submit(self, spec: ExecutionSpec) -> Execution:
        assert isinstance(spec, TaskSpec)
        options = spec.options_for("sqs")

        request: dict[str, Any] = {
            "QueueUrl": self._queue_url,
            "MessageBody": json.dumps(build_envelope(spec)),
        }
        if options.get("message_attributes"):
            request["MessageAttributes"] = options["message_attributes"]

        if self.is_fifo:
            # DelaySeconds is rejected outright by FIFO queues, so a deferred
            # spec must not reach one. The capability set already excludes DELAY,
            # which means BaseBackend.validate refuses it before we get here —
            # this is the belt to that braces.
            group = options.get("message_group_id") or self._message_group_id or spec.queue
            request["MessageGroupId"] = str(group)
            dedup = options.get("deduplication_id") or spec.idempotency_key
            if dedup is not None:
                request["MessageDeduplicationId"] = str(dedup)
        else:
            delay = self._delay_seconds(spec)
            if delay:
                request["DelaySeconds"] = delay

        try:
            response = self._sqs().send_message(**request)
        except Exception as exc:
            raise SubmissionError(
                f"SQS could not send {spec.task!r} to {self._queue_url}: {exc}",
                backend=self._name,
            ) from exc

        message_id = response.get("MessageId") if isinstance(response, dict) else None
        return Execution(
            id=new_execution_id(ExecutionKind.TASK),
            kind=ExecutionKind.TASK,
            backend=self._name,
            # The last thing this backend can honestly observe: SQS will not tell
            # us anything about this message again.
            state=ExecutionState.QUEUED,
            name=spec.name,
            created_at=datetime.now(UTC),
            external_id=str(message_id) if message_id else None,
            correlation=spec.correlation,
            provider_metadata=ProviderMetadata(
                provider="aws",
                provider_id=str(message_id) if message_id else None,
                region=self._region,
                resource=self._queue_url,
                labels=dict(spec.labels),
            ),
            metadata={"queue": spec.queue, "queue_url": self._queue_url},
        )


def make_backend(**options: Any) -> SQSTaskBackend:
    """Entry point for ``{"factory": "sqs", ...}`` configuration."""
    return SQSTaskBackend(
        queue_url=options.get("queue_url"),
        region=options.get("region"),
        client=options.get("client"),
        message_group_id=options.get("message_group_id"),
        name=str(options.get("name", "sqs")),
    )


__all__ = [
    "FIFO_CAPABILITIES",
    "MAX_DELAY_SECONDS",
    "SQS_CAPABILITIES",
    "SQSTaskBackend",
    "make_backend",
]
