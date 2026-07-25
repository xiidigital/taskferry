"""Tests for taskport-django backends, including the reusable contract."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.tasks import task_backends
from django.tasks.exceptions import InvalidTask
from django.utils import timezone

import fakes
import tasks  # module-level test tasks (on pythonpath)
from taskport.core import ConfigurationError
from taskport.django.backends.cloud_tasks import CloudTasksBackend
from taskport.django.backends.local import LocalBackend
from taskport.django.backends.sqs import SQSBackend
from taskport.django.capabilities import TaskCapability
from taskport.django.contract import TaskBackendContract


# --------------------------------------------------------------------------- #
# Contract suites (backends configured in TASKS with fake clients)
# --------------------------------------------------------------------------- #
class TestCloudTasksContract(TaskBackendContract):
    backend_alias = "cloudtasks"

    def sample_task(self):  # type: ignore[no-untyped-def]
        return tasks.record


class TestSQSContract(TaskBackendContract):
    backend_alias = "sqs"

    def sample_task(self):  # type: ignore[no-untyped-def]
        return tasks.record


# --------------------------------------------------------------------------- #
# Local backend
# --------------------------------------------------------------------------- #
def test_local_backend_runs_immediately_and_reports_capabilities() -> None:
    tasks.CALLS.clear()
    result = tasks.add.enqueue(2, 3)
    assert result.return_value == 5
    assert tasks.CALLS == [5]

    backend = LocalBackend("default", {})
    caps = backend.taskport_capabilities
    assert caps.provider == "local"
    assert TaskCapability.PRIORITY in caps
    assert TaskCapability.ASYNC_ENQUEUE in caps
    assert TaskCapability.RESULT_TRACKING not in caps


# --------------------------------------------------------------------------- #
# Cloud Tasks specifics
# --------------------------------------------------------------------------- #
def test_cloud_tasks_builds_http_push_request() -> None:
    fakes.CLOUD_TASKS_CLIENT.reset()
    backend = task_backends["cloudtasks"]
    run_after = timezone.now() + timedelta(seconds=60)
    deferred = tasks.record.using(backend="cloudtasks", run_after=run_after)
    backend.enqueue(deferred, [7], {})

    request = fakes.CLOUD_TASKS_CLIENT.created[-1]
    assert request["parent"].endswith("/queues/default")
    http = request["task"]["http_request"]
    assert http["url"].endswith("/_taskport/execute")
    body = json.loads(http["body"])
    assert body["module_path"] == "tasks.record"
    assert body["args"] == [7]
    assert body["correlation"] is not None  # correlation propagated
    assert request["task"]["schedule_time"] == run_after


def test_cloud_tasks_oidc_token_when_service_account_set() -> None:
    client = fakes.FakeCloudTasksClient()
    backend = CloudTasksBackend(
        "default",
        {
            "OPTIONS": {
                "project": "p",
                "location": "l",
                "queue": "q",
                "url": "https://x",
                "client": client,
                "service_account_email": "run@p.iam.gserviceaccount.com",
            }
        },
    )
    backend.enqueue(tasks.record, [1], {})
    http = client.created[0]["task"]["http_request"]
    assert http["oidc_token"]["service_account_email"] == "run@p.iam.gserviceaccount.com"


def test_cloud_tasks_requires_options() -> None:
    backend = CloudTasksBackend("default", {"OPTIONS": {"client": fakes.FakeCloudTasksClient()}})
    with pytest.raises(ConfigurationError):
        backend.enqueue(tasks.record, [1], {})


# --------------------------------------------------------------------------- #
# SQS specifics
# --------------------------------------------------------------------------- #
def test_sqs_sends_message_with_delay() -> None:
    fakes.SQS_CLIENT.reset()
    backend = task_backends["sqs"]
    deferred = tasks.record.using(backend="sqs", run_after=timezone.now() + timedelta(seconds=30))
    result = backend.enqueue(deferred, [9], {})
    assert result.id == "m-1"
    sent = fakes.SQS_CLIENT.sent[-1]
    assert 25 <= sent["DelaySeconds"] <= 30
    assert json.loads(sent["MessageBody"])["args"] == [9]


def test_sqs_rejects_delay_beyond_limit() -> None:
    client = fakes.FakeSQSClient()
    backend = SQSBackend("default", {"OPTIONS": {"queue_url": "https://sqs/q", "client": client}})
    # Build the deferred task via the sqs alias (supports_defer) then enqueue on a
    # backend instance whose limit rejects it.
    deferred = tasks.record.using(backend="sqs", run_after=timezone.now() + timedelta(hours=1))
    with pytest.raises(InvalidTask):
        backend.enqueue(deferred, [1], {})


def test_sqs_fifo_sets_group_and_advertises_ordering() -> None:
    client = fakes.FakeSQSClient()
    backend = SQSBackend(
        "default",
        {
            "OPTIONS": {
                "queue_url": "https://sqs.us-east-1.amazonaws.com/1/q.fifo",
                "client": client,
            }
        },
    )
    assert TaskCapability.ORDERING in backend.taskport_capabilities
    backend.enqueue(tasks.record, [1], {})
    sent = client.sent[0]
    assert "MessageGroupId" in sent
    assert "DelaySeconds" not in sent  # not allowed on FIFO


def test_sqs_requires_queue_url() -> None:
    backend = SQSBackend("default", {"OPTIONS": {"client": fakes.FakeSQSClient()}})
    with pytest.raises(ConfigurationError):
        backend.enqueue(tasks.record, [1], {})
