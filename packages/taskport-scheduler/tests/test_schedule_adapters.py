"""Tests for the AWS EventBridge Scheduler and Kubernetes CronJob adapters."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from taskport.core import ConfigurationError, ProviderOptions, UnsupportedCapabilityError
from taskport_scheduler import (
    CronTrigger,
    HttpTarget,
    IntervalTrigger,
    OneShotTrigger,
    Schedule,
    ScheduleStatus,
)
from taskport_scheduler.adapters.aws import EventBridgeScheduler, build_schedule_expression
from taskport_scheduler.adapters.kubernetes import (
    KubernetesCronJobScheduler,
    status_from_suspend,
)
from taskport_scheduler.contract import SchedulerContract

_AWS_OPTS = ProviderOptions(
    {"aws": {"target_arn": "arn:aws:lambda:...:fn", "role_arn": "arn:aws:iam::...:role/r"}}
)


# --------------------------------------------------------------------------- #
# AWS EventBridge Scheduler
# --------------------------------------------------------------------------- #
class _FakeSchedulerClient:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.updated: list[dict] = []
        self.deleted: str | None = None

    def create_schedule(self, **kwargs: object) -> dict:
        self.created.append(kwargs)
        return {"ScheduleArn": "arn:aws:scheduler:::schedule/default/x"}

    def get_schedule(self, Name: str) -> dict:
        return {
            "Name": Name,
            "ScheduleExpression": "cron(0 2 * * ? *)",
            "Target": {"Arn": "a", "RoleArn": "r"},
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "State": "ENABLED",
            "Arn": "arn:aws:scheduler:::schedule/default/" + Name,
        }

    def update_schedule(self, **kwargs: object) -> dict:
        self.updated.append(kwargs)
        return {}

    def delete_schedule(self, Name: str) -> None:
        self.deleted = Name

    def list_schedules(self) -> dict:
        return {"Schedules": [{"Name": "x", "State": "ENABLED", "Arn": "arn:x"}]}


def _aws(client: _FakeSchedulerClient | None = None) -> EventBridgeScheduler:
    return EventBridgeScheduler(region="us-east-1", client=client or _FakeSchedulerClient())


def test_aws_create_builds_cron_and_target() -> None:
    client = _FakeSchedulerClient()
    scheduler = _aws(client)
    scheduler.create(
        Schedule(
            "nightly",
            CronTrigger("0 2 * * ? *", timezone="UTC"),
            HttpTarget("ignored"),
            provider_options=_AWS_OPTS,
        )
    )
    params = client.created[0]
    assert params["ScheduleExpression"] == "cron(0 2 * * ? *)"
    assert params["Target"]["Arn"] == "arn:aws:lambda:...:fn"
    assert params["State"] == "ENABLED"


def test_aws_requires_target_arn_and_role() -> None:
    scheduler = _aws()
    with pytest.raises(ConfigurationError):
        scheduler.create(Schedule("x", CronTrigger("0 2 * * ? *"), HttpTarget("i")))


def test_aws_pause_resume_delete() -> None:
    client = _FakeSchedulerClient()
    scheduler = _aws(client)
    handle = scheduler.create(
        Schedule("n", CronTrigger("0 2 * * ? *"), HttpTarget("i"), provider_options=_AWS_OPTS)
    )
    assert scheduler.pause(handle).status == ScheduleStatus.PAUSED
    assert client.updated[-1]["State"] == "DISABLED"
    assert scheduler.resume(handle).status == ScheduleStatus.ENABLED
    scheduler.delete(handle)
    assert client.deleted == "n"
    assert len(scheduler.list()) == 1


@pytest.mark.parametrize(
    "schedule,expected_prefix",
    [
        (Schedule("c", CronTrigger("0 2 * * ? *"), HttpTarget("i")), "cron("),
        (Schedule("i", IntervalTrigger(120), HttpTarget("i")), "rate(2 minutes)"),
    ],
)
def test_aws_expression_mapping(schedule: Schedule, expected_prefix: str) -> None:
    expr, _ = build_schedule_expression(schedule)
    assert expr.startswith(expected_prefix)


def test_aws_oneshot_expression() -> None:
    from datetime import UTC, datetime

    schedule = Schedule(
        "o", OneShotTrigger(datetime(2027, 1, 2, 3, 4, 5, tzinfo=UTC)), HttpTarget("i")
    )
    expr, _ = build_schedule_expression(schedule)
    assert expr == "at(2027-01-02T03:04:05)"


class TestEventBridgeSchedulerContract(SchedulerContract):
    def make_scheduler(self) -> EventBridgeScheduler:
        return _aws()

    def sample_schedule(self) -> Schedule:
        return Schedule(
            "c", CronTrigger("0 * * * ? *"), HttpTarget("i"), provider_options=_AWS_OPTS
        )


# --------------------------------------------------------------------------- #
# Kubernetes CronJob
# --------------------------------------------------------------------------- #
class _FakeCronApi:
    def __init__(self) -> None:
        self.created: dict | None = None
        self.patched: list[dict] = []
        self.deleted: str | None = None

    def create_namespaced_cron_job(self, namespace: str, body: dict) -> None:
        self.created = body

    def read_namespaced_cron_job(self, name: str, namespace: str) -> object:
        return SimpleNamespace(spec=SimpleNamespace(suspend=False))

    def patch_namespaced_cron_job(self, name: str, namespace: str, body: dict) -> None:
        self.patched.append(body)

    def delete_namespaced_cron_job(self, name: str, namespace: str) -> None:
        self.deleted = name

    def list_namespaced_cron_job(self, namespace: str) -> object:
        item = SimpleNamespace(
            metadata=SimpleNamespace(name="beat"), spec=SimpleNamespace(suspend=True)
        )
        return SimpleNamespace(items=[item])


_K8S_OPTS = ProviderOptions(
    {"kubernetes": {"image": "repo/reporter:1", "command": ["python", "report.py"]}}
)


def _k8s(api: _FakeCronApi | None = None) -> KubernetesCronJobScheduler:
    return KubernetesCronJobScheduler(namespace="ops", api=api or _FakeCronApi())


def test_k8s_create_builds_cronjob_body() -> None:
    api = _FakeCronApi()
    scheduler = _k8s(api)
    scheduler.create(
        Schedule(
            "report",
            CronTrigger("0 6 * * *", timezone="Europe/Madrid"),
            HttpTarget("i"),
            provider_options=_K8S_OPTS,
        )
    )
    body = api.created
    assert body["kind"] == "CronJob"
    assert body["spec"]["schedule"] == "0 6 * * *"
    assert body["spec"]["timeZone"] == "Europe/Madrid"
    container = body["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "repo/reporter:1"
    assert container["args"] == ["python", "report.py"]


def test_k8s_requires_image() -> None:
    scheduler = _k8s()
    with pytest.raises(ConfigurationError):
        scheduler.create(Schedule("x", CronTrigger("* * * * *"), HttpTarget("i")))


def test_k8s_rejects_interval_trigger() -> None:
    scheduler = _k8s()
    with pytest.raises(UnsupportedCapabilityError):
        scheduler.create(
            Schedule("x", IntervalTrigger(60), HttpTarget("i"), provider_options=_K8S_OPTS)
        )


def test_k8s_pause_resume_delete_list() -> None:
    api = _FakeCronApi()
    scheduler = _k8s(api)
    handle = scheduler.create(
        Schedule("report", CronTrigger("0 6 * * *"), HttpTarget("i"), provider_options=_K8S_OPTS)
    )
    assert scheduler.pause(handle).status == ScheduleStatus.PAUSED
    assert api.patched[-1] == {"spec": {"suspend": True}}
    assert scheduler.resume(handle).status == ScheduleStatus.ENABLED
    listed = scheduler.list()
    assert listed[0].status == ScheduleStatus.PAUSED  # from fake list (suspend=True)
    scheduler.delete(handle)
    assert api.deleted == "report"


def test_status_from_suspend() -> None:
    assert status_from_suspend(True) == ScheduleStatus.PAUSED
    assert status_from_suspend(False) == ScheduleStatus.ENABLED
    assert status_from_suspend(None) == ScheduleStatus.ENABLED


class TestKubernetesCronJobContract(SchedulerContract):
    def make_scheduler(self) -> KubernetesCronJobScheduler:
        return _k8s()

    def sample_schedule(self) -> Schedule:
        return Schedule("c", CronTrigger("0 * * * *"), HttpTarget("i"), provider_options=_K8S_OPTS)
