"""The Azure Service Bus adapter, tested with an injected client and factory."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from taskferry import BackendOptions, Capability, ExecutionState, TaskSpec
from taskferry.contract import TaskBackendContract
from taskferry.envelope import ENVELOPE_VERSION
from taskferry.errors import ConfigurationError, SubmissionError, UnsupportedCapability
from taskferry.functions import FunctionRegistry
from taskferry.ports import ExecutionBackend
from taskferry_servicebus import ServiceBusTaskBackend, process_message, receive_forever


class FakeMessage(SimpleNamespace):
    """Stands in for ``ServiceBusMessage``; records everything it was given."""


def fake_message_factory(body: str, **kwargs: Any) -> FakeMessage:
    return FakeMessage(body=body, **kwargs)


class FakeSender:
    def __init__(self, sink: list[FakeMessage], *, fail: bool = False) -> None:
        self._sink = sink
        self._fail = fail

    def send_messages(self, message: FakeMessage) -> None:
        if self._fail:
            raise RuntimeError("ServiceBusError: entity not found")
        self._sink.append(message)


class FakeReceiver:
    def __init__(self, messages: list[Any]) -> None:
        self._messages = messages
        self.completed: list[Any] = []
        self.abandoned: list[Any] = []

    def __enter__(self) -> FakeReceiver:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def receive_messages(self, **_: Any) -> list[Any]:
        batch, self._messages = self._messages, []
        return batch

    def complete_message(self, message: Any) -> None:
        self.completed.append(message)

    def abandon_message(self, message: Any) -> None:
        self.abandoned.append(message)


class FakeServiceBus:
    def __init__(self, *, fail: bool = False, messages: list[Any] | None = None) -> None:
        self.sent: list[FakeMessage] = []
        self.fail = fail
        self.receiver = FakeReceiver(messages or [])

    def get_queue_sender(self, queue_name: str) -> FakeSender:
        return FakeSender(self.sent, fail=self.fail)

    def get_queue_receiver(self, *, queue_name: str) -> FakeReceiver:
        return self.receiver


def backend(**overrides: Any) -> ServiceBusTaskBackend:
    return ServiceBusTaskBackend(
        queue_name=overrides.pop("queue_name", "tasks"),
        client=overrides.pop("client", FakeServiceBus()),
        message_factory=overrides.pop("message_factory", fake_message_factory),
        **overrides,
    )


class TestServiceBusContract(TaskBackendContract):
    def make_backend(self) -> ExecutionBackend:
        return backend()

    def success_spec(self) -> TaskSpec:
        return TaskSpec(task="myapp.tasks:reindex", args=(42,))


class TestSending:
    def test_the_body_is_the_portable_envelope(self) -> None:
        client = FakeServiceBus()
        backend(client=client).submit(TaskSpec(task="myapp:reindex", args=(42,)))
        body = json.loads(client.sent[0].body)
        assert body["taskferry"] == ENVELOPE_VERSION
        assert body["task"] == "myapp:reindex"
        assert body["args"] == [42]

    def test_task_metadata_travels_as_application_properties(self) -> None:
        """So an operator can filter the queue without decoding every body."""
        client = FakeServiceBus()
        backend(client=client).submit(TaskSpec(task="myapp:reindex", queue="metadata"))
        properties = client.sent[0].application_properties
        assert properties["taskferry-task"] == "myapp:reindex"
        assert properties["taskferry-queue"] == "metadata"

    def test_a_send_failure_becomes_a_submission_error(self) -> None:
        with pytest.raises(SubmissionError) as caught:
            backend(client=FakeServiceBus(fail=True)).submit(TaskSpec(task="m:f"))
        assert isinstance(caught.value.__cause__, RuntimeError)

    def test_a_missing_queue_name_is_a_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError, match="queue_name"):
            ServiceBusTaskBackend(client=FakeServiceBus())

    def test_missing_authentication_is_a_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError, match="connection_string"):
            ServiceBusTaskBackend(queue_name="tasks")

    def test_managed_identity_configuration_is_accepted(self) -> None:
        target = ServiceBusTaskBackend(
            queue_name="tasks",
            namespace="myns.servicebus.windows.net",
            credential=object(),
            message_factory=fake_message_factory,
        )
        assert target.queue_name == "tasks"


class TestSchedulingIsUnbounded:
    """The difference from SQS that makes this a separate adapter."""

    def test_a_delay_becomes_a_scheduled_enqueue_time(self) -> None:
        client = FakeServiceBus()
        backend(client=client).submit(TaskSpec(task="m:f", delay=timedelta(minutes=30)))
        assert client.sent[0].scheduled_enqueue_time_utc is not None

    def test_a_week_long_delay_is_accepted(self) -> None:
        """SQS caps at 900 seconds; Service Bus does not, and the adapter says so."""
        client = FakeServiceBus()
        execution = backend(client=client).submit(TaskSpec(task="m:f", delay=timedelta(days=7)))
        assert execution.state is ExecutionState.QUEUED
        assert client.sent[0].scheduled_enqueue_time_utc is not None

    def test_an_immediate_task_schedules_nothing(self) -> None:
        client = FakeServiceBus()
        backend(client=client).submit(TaskSpec(task="m:f"))
        assert client.sent[0].scheduled_enqueue_time_utc is None


class TestDeduplication:
    def test_an_idempotency_key_becomes_the_message_id(self) -> None:
        """Which is what Service Bus actually deduplicates on."""
        client = FakeServiceBus()
        execution = backend(client=client).submit(TaskSpec(task="m:f", idempotency_key="order-42"))
        assert client.sent[0].message_id == "order-42"
        assert execution.external_id == "order-42"

    def test_without_a_key_a_unique_id_is_minted(self) -> None:
        client = FakeServiceBus()
        backend(client=client).submit(TaskSpec(task="m:f"))
        assert client.sent[0].message_id.startswith("sbmsg_")


class TestBackendOptions:
    def test_a_session_id_passes_through(self) -> None:
        client = FakeServiceBus()
        backend(client=client).submit(
            TaskSpec(
                task="m:f",
                backend_options=BackendOptions({"servicebus": {"session_id": "tenant-7"}}),
            )
        )
        assert client.sent[0].session_id == "tenant-7"

    def test_another_engines_options_are_ignored(self) -> None:
        client = FakeServiceBus()
        backend(client=client).submit(
            TaskSpec(
                task="m:f",
                backend_options=BackendOptions({"sqs": {"message_group_id": "irrelevant"}}),
            )
        )
        assert not hasattr(client.sent[0], "message_group_id")


class TestCapabilities:
    def test_delay_and_deduplication_are_advertised(self) -> None:
        caps = backend().capabilities
        assert Capability.DELAY in caps
        assert Capability.DEDUPLICATION in caps

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
        envelope = {"taskferry": ENVELOPE_VERSION, "task": "tests:add", "args": [20, 22]}
        assert process_message(json.dumps(envelope), registry=registry) == 42

    def test_bytes_are_accepted(self) -> None:
        registry = FunctionRegistry()
        registry.register(lambda: "ok", name="tests:ok")
        envelope = {"taskferry": ENVELOPE_VERSION, "task": "tests:ok"}
        assert process_message(json.dumps(envelope).encode(), registry=registry) == "ok"

    def test_the_sdk_generator_body_shape_is_accepted(self) -> None:
        """``ServiceBusReceivedMessage.body`` yields byte chunks."""
        registry = FunctionRegistry()
        registry.register(lambda: "ok", name="tests:ok")
        raw = json.dumps({"taskferry": ENVELOPE_VERSION, "task": "tests:ok"}).encode()
        chunks = (raw[i : i + 8] for i in range(0, len(raw), 8))
        assert process_message(chunks, registry=registry) == "ok"

    def test_a_malformed_body_is_rejected(self) -> None:
        from taskferry.errors import SerializationError

        with pytest.raises(SerializationError, match="not valid JSON"):
            process_message("{not json")

    def test_an_allowlist_blocks_an_arbitrary_import(self) -> None:
        from taskferry.errors import FunctionResolutionError

        registry = FunctionRegistry(allowed_modules=["myapp"])
        envelope = {"taskferry": ENVELOPE_VERSION, "task": "os:system", "args": ["echo pwned"]}
        with pytest.raises(FunctionResolutionError, match="allowlist"):
            process_message(json.dumps(envelope), registry=registry)

    def test_receive_forever_completes_on_success(self) -> None:
        registry = FunctionRegistry()
        registry.register(lambda: "ok", name="tests:ok")
        body = json.dumps({"taskferry": ENVELOPE_VERSION, "task": "tests:ok"}).encode()
        client = FakeServiceBus(messages=[SimpleNamespace(message_id="m1", body=body)])
        calls = {"n": 0}

        def stop() -> bool:
            calls["n"] += 1
            return calls["n"] > 1

        assert receive_forever(client, "tasks", registry=registry, should_stop=stop) == 1
        assert len(client.receiver.completed) == 1
        assert client.receiver.abandoned == []

    def test_receive_forever_abandons_a_failure_for_redelivery(self) -> None:
        body = json.dumps({"taskferry": ENVELOPE_VERSION, "task": "tests:missing"}).encode()
        client = FakeServiceBus(messages=[SimpleNamespace(message_id="m1", body=body)])
        calls = {"n": 0}

        def stop() -> bool:
            calls["n"] += 1
            return calls["n"] > 1

        assert receive_forever(client, "tasks", should_stop=stop) == 0
        assert len(client.receiver.abandoned) == 1
        assert client.receiver.completed == []
