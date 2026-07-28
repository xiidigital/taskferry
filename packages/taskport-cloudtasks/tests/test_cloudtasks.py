"""The Cloud Tasks adapter — and the proof that swapping engines changes nothing.

The most important test in this file is
:meth:`TestEnginePortability.test_the_same_spec_reaches_two_different_engines`.
Everything else supports it.
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from taskport import BackendOptions, Capability, ExecutionState, TaskSpec
from taskport.contract import TaskBackendContract
from taskport.core.correlation import Correlation
from taskport.errors import ConfigurationError, SerializationError, SubmissionError
from taskport.functions import FunctionRegistry
from taskport.ports import ExecutionBackend
from taskport_cloudtasks import CloudTasksBackend, handle_request, parse_request
from taskport_cloudtasks.backend import MESSAGE_VERSION, build_message


class FakeTasksClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.created: list[dict[str, Any]] = []
        self.fail = fail

    def queue_path(self, project: str, location: str, queue: str) -> str:
        return f"projects/{project}/locations/{location}/queues/{queue}"

    def create_task(self, *, request: dict[str, Any]) -> SimpleNamespace:
        if self.fail:
            raise RuntimeError("RESOURCE_EXHAUSTED: queue is full")
        self.created.append(request)
        return SimpleNamespace(name=f"{request['parent']}/tasks/{len(self.created)}")


def make_backend(**overrides: Any) -> CloudTasksBackend:
    return CloudTasksBackend(
        project=overrides.pop("project", "my-project"),
        location=overrides.pop("location", "europe-west1"),
        url=overrides.pop("url", "https://svc.run.app/_taskport/execute"),
        client=overrides.pop("client", FakeTasksClient()),
        **overrides,
    )


class TestCloudTasksContract(TaskBackendContract):
    def make_backend(self) -> ExecutionBackend:
        return make_backend()

    def success_spec(self) -> TaskSpec:
        return TaskSpec(task="myapp.tasks:send_email", args=(42,))


class TestSubmission:
    def test_the_body_carries_the_portable_message(self) -> None:
        client = FakeTasksClient()
        backend = make_backend(client=client)
        backend.submit(TaskSpec(task="myapp.tasks:send_email", args=(42,), kwargs={"cc": "a@b"}))
        body = json.loads(client.created[0]["task"]["http_request"]["body"])
        assert body["task"] == "myapp.tasks:send_email"
        assert body["args"] == [42]
        assert body["kwargs"] == {"cc": "a@b"}

    def test_the_queue_selects_a_real_cloud_tasks_queue(self) -> None:
        client = FakeTasksClient()
        backend = make_backend(client=client)
        backend.submit(TaskSpec(task="m:f", queue="email"))
        assert client.created[0]["parent"].endswith("/queues/email")

    def test_a_delay_becomes_a_schedule_time(self) -> None:
        client = FakeTasksClient()
        backend = make_backend(client=client)
        backend.submit(TaskSpec(task="m:f", delay=timedelta(minutes=10)))
        assert "schedule_time" in client.created[0]["task"]

    def test_an_idempotency_key_becomes_a_task_name(self) -> None:
        """Cloud Tasks' own deduplication: a name is unique per queue."""
        client = FakeTasksClient()
        backend = make_backend(client=client)
        backend.submit(TaskSpec(task="m:f", idempotency_key="order-42"))
        assert client.created[0]["task"]["name"].endswith("/tasks/order-42")

    def test_an_unsafe_idempotency_key_is_sanitised(self) -> None:
        client = FakeTasksClient()
        backend = make_backend(client=client)
        backend.submit(TaskSpec(task="m:f", idempotency_key="order/42 #1"))
        assert client.created[0]["task"]["name"].endswith("/tasks/order-42--1")

    def test_an_oidc_token_is_attached_when_a_service_account_is_configured(self) -> None:
        """Without this the endpoint is open to anyone who finds the URL."""
        client = FakeTasksClient()
        backend = make_backend(client=client, service_account_email="runner@p.iam.example")
        backend.submit(TaskSpec(task="m:f"))
        oidc = client.created[0]["task"]["http_request"]["oidc_token"]
        assert oidc["service_account_email"] == "runner@p.iam.example"
        assert oidc["audience"] == "https://svc.run.app/_taskport/execute"

    def test_correlation_travels_as_http_headers(self) -> None:
        """So a trace survives the hop through Google's infrastructure."""
        client = FakeTasksClient()
        backend = make_backend(client=client)
        correlation = Correlation.start()
        backend.submit(TaskSpec(task="m:f", correlation=correlation))
        headers = client.created[0]["task"]["http_request"]["headers"]
        assert headers["taskport-correlation-id"] == correlation.correlation_id

    def test_backend_options_pass_through(self) -> None:
        client = FakeTasksClient()
        backend = make_backend(client=client)
        backend.submit(
            TaskSpec(
                task="m:f",
                backend_options=BackendOptions({"cloudtasks": {"dispatch_deadline": 900}}),
            )
        )
        assert client.created[0]["task"]["dispatch_deadline"] == 900

    def test_a_create_failure_becomes_a_submission_error(self) -> None:
        backend = make_backend(client=FakeTasksClient(fail=True))
        with pytest.raises(SubmissionError) as caught:
            backend.submit(TaskSpec(task="m:f"))
        assert isinstance(caught.value.__cause__, RuntimeError)

    def test_the_state_stops_at_queued(self) -> None:
        """The last thing this backend can honestly observe."""
        backend = make_backend()
        assert backend.submit(TaskSpec(task="m:f")).state is ExecutionState.QUEUED


class TestCapabilities:
    def test_state_and_result_are_not_advertised(self) -> None:
        """Cloud Tasks reports nothing per task after creation, and this says so."""
        capabilities = make_backend().capabilities
        assert Capability.STATE not in capabilities
        assert Capability.RESULT not in capabilities

    def test_asking_for_state_is_refused_not_faked(self) -> None:
        """Better a clear refusal than a plausible UNKNOWN forever."""
        from taskport.errors import UnsupportedCapability

        backend = make_backend()
        execution = backend.submit(TaskSpec(task="m:f"))
        with pytest.raises(UnsupportedCapability):
            backend.get(execution.id)

    def test_what_cloud_tasks_really_supports_is_advertised(self) -> None:
        capabilities = make_backend().capabilities
        for supported in (Capability.DELAY, Capability.RETRY, Capability.DEDUPLICATION):
            assert supported in capabilities


class TestConfiguration:
    def test_missing_options_are_named_individually(self) -> None:
        with pytest.raises(ConfigurationError, match="project, location, url"):
            CloudTasksBackend()

    def test_the_url_requirement_is_explained(self) -> None:
        with pytest.raises(ConfigurationError, match="Cloud Tasks will POST"):
            CloudTasksBackend(project="p", location="eu")


class TestReceiver:
    def test_a_pushed_request_runs_the_task(self) -> None:
        registry = FunctionRegistry()
        registry.register(lambda a, b: a + b, name="tests:add")
        body = json.dumps(
            {"taskport": MESSAGE_VERSION, "task": "tests:add", "args": [20, 22]}
        ).encode()
        assert handle_request(body, registry=registry) == 42

    def test_a_malformed_body_is_rejected_loudly(self) -> None:
        """A misconfigured endpoint must not look like a successful no-op."""
        with pytest.raises(SerializationError, match="not valid JSON"):
            parse_request(b"{not json")

    def test_a_version_mismatch_is_rejected(self) -> None:
        with pytest.raises(SerializationError, match="unsupported taskport message version"):
            parse_request(json.dumps({"taskport": "99", "task": "m:f"}).encode())

    def test_a_body_without_a_task_is_rejected(self) -> None:
        with pytest.raises(SerializationError, match="no 'task'"):
            parse_request(json.dumps({"taskport": MESSAGE_VERSION}).encode())

    def test_an_allowlist_blocks_an_arbitrary_import(self) -> None:
        """This endpoint is reachable from the network. The allowlist is the point."""
        from taskport.errors import FunctionResolutionError

        registry = FunctionRegistry(allowed_modules=["myapp"])
        body = json.dumps(
            {"taskport": MESSAGE_VERSION, "task": "os:system", "args": ["echo pwned"]}
        ).encode()
        with pytest.raises(FunctionResolutionError, match="allowlist"):
            handle_request(body, registry=registry)

    def test_headers_restore_the_correlation(self) -> None:
        from taskport.core.correlation import current_correlation

        seen: list[str] = []
        registry = FunctionRegistry()
        registry.register(
            lambda: seen.append(current_correlation().correlation_id),  # type: ignore[union-attr]
            name="tests:observe",
        )
        correlation = Correlation.start()
        body = json.dumps({"taskport": MESSAGE_VERSION, "task": "tests:observe"}).encode()
        handle_request(body, correlation.to_headers(), registry=registry)
        assert seen == [correlation.correlation_id]

    def test_the_body_carries_correlation_when_headers_do_not(self) -> None:
        from taskport.core.correlation import current_correlation

        seen: list[str] = []
        registry = FunctionRegistry()
        registry.register(
            lambda: seen.append(current_correlation().correlation_id),  # type: ignore[union-attr]
            name="tests:observe",
        )
        correlation = Correlation.start()
        message = build_message(TaskSpec(task="tests:observe", correlation=correlation))
        handle_request(json.dumps(message).encode(), registry=registry)
        assert seen == [correlation.correlation_id]


class TestEnginePortability:
    """The claim the whole project rests on, tested directly."""

    def test_the_same_spec_reaches_two_different_engines(self) -> None:
        """One spec object, two engines, no edits.

        Procrastinate polls PostgreSQL and runs a worker. Cloud Tasks pushes an
        HTTP request and runs no worker at all. If the spec needed even one field
        changed between them, the abstraction would be decorative.
        """
        from taskport_procrastinate import ProcrastinateTaskBackend

        spec = TaskSpec(
            task="myapp.tasks:send_email",
            args=(42,),
            kwargs={"template": "welcome"},
            queue="email",
            delay=timedelta(minutes=5),
            idempotency_key="welcome-42",
            backend_options=BackendOptions(
                {
                    "procrastinate": {"lock": "user-42"},
                    "cloudtasks": {"dispatch_deadline": 900},
                }
            ),
        )

        class Deferrer:
            def __init__(self, kwargs: dict[str, Any], sink: list[dict[str, Any]]) -> None:
                self.kwargs, self.sink = kwargs, sink

            def defer(self, **payload: Any) -> int:
                self.sink.append({**self.kwargs, **payload})
                return 1

        deferred: list[dict[str, Any]] = []

        class App:
            def configure_task(self, **kwargs: Any) -> Deferrer:
                return Deferrer(kwargs, deferred)

        pg = ProcrastinateTaskBackend(app=App())
        push_client = FakeTasksClient()
        push = make_backend(client=push_client)

        pg_execution = pg.submit(spec)
        push_execution = push.submit(spec)

        # Both accepted the identical spec.
        assert pg_execution.state is ExecutionState.QUEUED
        assert push_execution.state is ExecutionState.QUEUED

        # Each honoured the portable fields in its own vocabulary...
        assert deferred[0]["queue"] == "email"
        assert push_client.created[0]["parent"].endswith("/queues/email")
        assert deferred[0]["queueing_lock"] == "welcome-42"
        assert push_client.created[0]["task"]["name"].endswith("/tasks/welcome-42")

        # ...read only its own options...
        assert deferred[0]["lock"] == "user-42"
        assert push_client.created[0]["task"]["dispatch_deadline"] == 900

        # ...and ignored the other engine's entirely.
        assert "dispatch_deadline" not in deferred[0]
        body = json.loads(push_client.created[0]["task"]["http_request"]["body"])
        assert "lock" not in body

    def test_the_capability_difference_is_visible_rather_than_hidden(self) -> None:
        """Same API, different honest answers — that is the capability model working."""
        from taskport_procrastinate import ProcrastinateTaskBackend

        pg = ProcrastinateTaskBackend(app=object())
        push = make_backend()

        assert Capability.STATE in pg.capabilities
        assert Capability.STATE not in push.capabilities
        assert Capability.PRIORITY in pg.capabilities
        assert Capability.PRIORITY not in push.capabilities
