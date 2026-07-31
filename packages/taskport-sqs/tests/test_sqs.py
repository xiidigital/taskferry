"""The SQS adapter, tested with a fake client and no AWS account."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest

from taskport import BackendOptions, Capability, ExecutionState, TaskSpec
from taskport.contract import TaskBackendContract
from taskport.envelope import ENVELOPE_VERSION
from taskport.errors import ConfigurationError, SubmissionError, UnsupportedCapability
from taskport.functions import FunctionRegistry
from taskport.ports import ExecutionBackend
from taskport_sqs import SQSTaskBackend, poll_forever, process_event, process_message

STANDARD_URL = "https://sqs.eu-west-1.amazonaws.com/123456789/tasks"
FIFO_URL = "https://sqs.eu-west-1.amazonaws.com/123456789/tasks.fifo"


class FakeSQS:
    def __init__(self, *, fail: bool = False, messages: list[dict[str, Any]] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.fail = fail
        self._messages = messages or []

    def send_message(self, **request: Any) -> dict[str, str]:
        if self.fail:
            raise RuntimeError("AWS.SimpleQueueService.NonExistentQueue")
        self.sent.append(request)
        return {"MessageId": f"msg-{len(self.sent)}"}

    def receive_message(self, **_: Any) -> dict[str, Any]:
        batch, self._messages = self._messages, []
        return {"Messages": batch}

    def delete_message(self, *, QueueUrl: str, ReceiptHandle: str) -> None:
        self.deleted.append(ReceiptHandle)


def backend(**overrides: Any) -> SQSTaskBackend:
    return SQSTaskBackend(
        queue_url=overrides.pop("queue_url", STANDARD_URL),
        client=overrides.pop("client", FakeSQS()),
        **overrides,
    )


class TestSQSContract(TaskBackendContract):
    def make_backend(self) -> ExecutionBackend:
        return backend()

    def success_spec(self) -> TaskSpec:
        return TaskSpec(task="myapp.tasks:send_email", args=(42,))


class TestSending:
    def test_the_body_is_the_portable_envelope(self) -> None:
        client = FakeSQS()
        backend(client=client).submit(TaskSpec(task="myapp:send", args=(42,), kwargs={"cc": "a"}))
        body = json.loads(client.sent[0]["MessageBody"])
        assert body["taskport"] == ENVELOPE_VERSION
        assert body["task"] == "myapp:send"
        assert body["args"] == [42]
        assert body["kwargs"] == {"cc": "a"}

    def test_a_delay_becomes_delay_seconds(self) -> None:
        client = FakeSQS()
        backend(client=client).submit(TaskSpec(task="m:f", delay=timedelta(minutes=5)))
        assert 295 <= client.sent[0]["DelaySeconds"] <= 300

    def test_an_immediate_task_sets_no_delay(self) -> None:
        client = FakeSQS()
        backend(client=client).submit(TaskSpec(task="m:f"))
        assert "DelaySeconds" not in client.sent[0]

    def test_message_attributes_pass_through(self) -> None:
        client = FakeSQS()
        backend(client=client).submit(
            TaskSpec(
                task="m:f",
                backend_options=BackendOptions(
                    {"sqs": {"message_attributes": {"Tenant": {"StringValue": "acme"}}}}
                ),
            )
        )
        assert client.sent[0]["MessageAttributes"]["Tenant"]["StringValue"] == "acme"

    def test_a_send_failure_becomes_a_submission_error(self) -> None:
        with pytest.raises(SubmissionError) as caught:
            backend(client=FakeSQS(fail=True)).submit(TaskSpec(task="m:f"))
        assert isinstance(caught.value.__cause__, RuntimeError)

    def test_the_message_id_is_kept_as_the_external_id(self) -> None:
        execution = backend().submit(TaskSpec(task="m:f"))
        assert execution.id.startswith("task_")
        assert execution.external_id == "msg-1"
        assert execution.state is ExecutionState.QUEUED

    def test_a_missing_queue_url_is_a_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError, match="queue_url"):
            SQSTaskBackend()


class TestTheDelayCeilingIsEnforcedNotClamped:
    """The limit is real, so the error is real."""

    def test_a_delay_over_fifteen_minutes_is_refused(self) -> None:
        with pytest.raises(SubmissionError) as caught:
            backend().submit(TaskSpec(task="m:f", delay=timedelta(hours=2)))
        assert "900s" in str(caught.value)
        assert "scheduler" in str(caught.value), "the error should say what to do instead"

    def test_nothing_was_sent_when_the_delay_is_refused(self) -> None:
        client = FakeSQS()
        with pytest.raises(SubmissionError):
            backend(client=client).submit(TaskSpec(task="m:f", delay=timedelta(hours=2)))
        assert client.sent == [], "a refused spec must not reach the queue"


class TestFifoIsGenuinelyDifferent:
    """The capability set is computed per queue, because the queues differ."""

    def test_a_fifo_queue_advertises_deduplication_and_not_delay(self) -> None:
        fifo = backend(queue_url=FIFO_URL)
        assert fifo.is_fifo
        assert Capability.DEDUPLICATION in fifo.capabilities
        assert Capability.DELAY not in fifo.capabilities

    def test_a_standard_queue_advertises_delay_and_not_deduplication(self) -> None:
        standard = backend()
        assert not standard.is_fifo
        assert Capability.DELAY in standard.capabilities
        assert Capability.DEDUPLICATION not in standard.capabilities

    def test_a_deferred_spec_is_refused_by_a_fifo_queue(self) -> None:
        """Rather than dropping the delay, which SQS would do silently."""
        with pytest.raises(UnsupportedCapability, match="delay"):
            backend(queue_url=FIFO_URL).submit(TaskSpec(task="m:f", delay=timedelta(seconds=30)))

    def test_an_idempotency_key_is_refused_by_a_standard_queue(self) -> None:
        with pytest.raises(UnsupportedCapability, match="deduplication"):
            backend().submit(TaskSpec(task="m:f", idempotency_key="k"))

    def test_fifo_sets_a_message_group_from_the_queue_name(self) -> None:
        client = FakeSQS()
        backend(queue_url=FIFO_URL, client=client).submit(TaskSpec(task="m:f", queue="email"))
        assert client.sent[0]["MessageGroupId"] == "email"

    def test_fifo_uses_the_idempotency_key_for_deduplication(self) -> None:
        client = FakeSQS()
        backend(queue_url=FIFO_URL, client=client).submit(
            TaskSpec(task="m:f", idempotency_key="order-42")
        )
        assert client.sent[0]["MessageDeduplicationId"] == "order-42"

    def test_fifo_never_sends_delay_seconds(self) -> None:
        """SQS rejects it outright on a FIFO queue."""
        client = FakeSQS()
        backend(queue_url=FIFO_URL, client=client).submit(TaskSpec(task="m:f"))
        assert "DelaySeconds" not in client.sent[0]


class TestCapabilities:
    def test_state_result_and_cancel_are_absent(self) -> None:
        caps = backend().capabilities
        for absent in (Capability.STATE, Capability.RESULT, Capability.CANCEL):
            assert absent not in caps

    def test_asking_for_state_is_refused(self) -> None:
        target = backend()
        execution = target.submit(TaskSpec(task="m:f"))
        with pytest.raises(UnsupportedCapability):
            target.get(execution.id)


class TestConsumer:
    def test_a_message_body_runs_its_task(self) -> None:
        registry = FunctionRegistry()
        registry.register(lambda a, b: a + b, name="tests:add")
        envelope = {"taskport": ENVELOPE_VERSION, "task": "tests:add", "args": [20, 22]}
        assert process_message(json.dumps(envelope), registry=registry) == 42

    def test_a_dict_body_is_accepted(self) -> None:
        registry = FunctionRegistry()
        registry.register(lambda: "ok", name="tests:ok")
        envelope = {"taskport": ENVELOPE_VERSION, "task": "tests:ok"}
        assert process_message(envelope, registry=registry) == "ok"

    def test_a_malformed_body_is_rejected(self) -> None:
        from taskport.errors import SerializationError

        with pytest.raises(SerializationError, match="not valid JSON"):
            process_message("{not json")

    def test_an_allowlist_blocks_an_arbitrary_import(self) -> None:
        from taskport.errors import FunctionResolutionError

        registry = FunctionRegistry(allowed_modules=["myapp"])
        envelope = {"taskport": ENVELOPE_VERSION, "task": "os:system", "args": ["echo pwned"]}
        with pytest.raises(FunctionResolutionError, match="allowlist"):
            process_message(json.dumps(envelope), registry=registry)

    def test_a_lambda_event_reports_partial_failures(self) -> None:
        """One poisonous message must not force the whole batch to redeliver."""
        registry = FunctionRegistry()
        registry.register(lambda: "ok", name="tests:ok")

        def event(*tasks: str) -> dict[str, Any]:
            return {
                "Records": [
                    {
                        "messageId": f"m{n}",
                        "body": json.dumps({"taskport": ENVELOPE_VERSION, "task": task}),
                    }
                    for n, task in enumerate(tasks)
                ]
            }

        result = process_event(event("tests:ok", "tests:missing", "tests:ok"), registry=registry)
        assert result["batchItemFailures"] == [{"itemIdentifier": "m1"}]

    def test_an_all_good_batch_reports_no_failures(self) -> None:
        registry = FunctionRegistry()
        registry.register(lambda: "ok", name="tests:ok")
        event = {
            "Records": [
                {
                    "messageId": "m0",
                    "body": json.dumps({"taskport": ENVELOPE_VERSION, "task": "tests:ok"}),
                }
            ]
        }
        assert process_event(event, registry=registry) == {"batchItemFailures": []}

    def test_poll_forever_deletes_on_success_and_stops_when_told(self) -> None:
        registry = FunctionRegistry()
        registry.register(lambda: "ok", name="tests:ok")
        body = json.dumps({"taskport": ENVELOPE_VERSION, "task": "tests:ok"})
        client = FakeSQS(
            messages=[
                {"MessageId": "m1", "ReceiptHandle": "r1", "Body": body},
                {"MessageId": "m2", "ReceiptHandle": "r2", "Body": body},
            ]
        )

        calls = {"n": 0}

        def stop() -> bool:
            calls["n"] += 1
            return calls["n"] > 1  # one batch, then exit

        processed = poll_forever(client, STANDARD_URL, registry=registry, should_stop=stop)
        assert processed == 2
        assert client.deleted == ["r1", "r2"]

    def test_poll_forever_leaves_a_failure_for_redelivery(self) -> None:
        """SQS's visibility timeout and redrive policy decide what happens next."""
        client = FakeSQS(
            messages=[
                {
                    "MessageId": "m1",
                    "ReceiptHandle": "r1",
                    "Body": json.dumps({"taskport": ENVELOPE_VERSION, "task": "tests:missing"}),
                }
            ]
        )
        calls = {"n": 0}

        def stop() -> bool:
            calls["n"] += 1
            return calls["n"] > 1

        processed = poll_forever(client, STANDARD_URL, should_stop=stop)
        assert processed == 0
        assert client.deleted == [], "a failed message must not be deleted"
