"""Tests for taskport_scheduler: triggers, LocalScheduler, Cloud Scheduler, contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from taskport.core import UnsupportedCapabilityError
from taskport_scheduler import (
    CallableTarget,
    CronTrigger,
    HttpTarget,
    IntervalTrigger,
    LocalScheduler,
    OneShotTrigger,
    Schedule,
    ScheduleCapability,
    ScheduleStatus,
    schedulers,
)
from taskport_scheduler.adapters.gcp import CloudSchedulerScheduler, map_scheduler_state
from taskport_scheduler.contract import SchedulerContract


# --------------------------------------------------------------------------- #
# Triggers
# --------------------------------------------------------------------------- #
def test_trigger_required_capabilities() -> None:
    assert CronTrigger("* * * * *").required_capability() == ScheduleCapability.CRON
    assert IntervalTrigger(5).required_capability() == ScheduleCapability.INTERVAL
    assert OneShotTrigger(datetime.now(UTC)).required_capability() == ScheduleCapability.ONE_SHOT


def test_interval_trigger_validates() -> None:
    with pytest.raises(ValueError):
        IntervalTrigger(0)


# --------------------------------------------------------------------------- #
# LocalScheduler
# --------------------------------------------------------------------------- #
def test_default_scheduler_is_local() -> None:
    assert schedulers["default"].provider == "local"


def test_local_interval_fires_and_repeats() -> None:
    scheduler = LocalScheduler()
    calls: list[int] = []
    handle = scheduler.create(
        Schedule("beat", IntervalTrigger(1), CallableTarget(lambda: calls.append(1)))
    )
    future = datetime.now(UTC) + timedelta(hours=1)
    assert len(scheduler.run_pending(future)) == 1
    assert calls == [1]
    scheduler.run_pending(future + timedelta(seconds=2))
    assert calls == [1, 1]
    assert len(scheduler.fires_for(handle)) == 2


def test_local_one_shot_fires_once() -> None:
    scheduler = LocalScheduler()
    calls: list[int] = []
    at = datetime.now(UTC) - timedelta(seconds=1)
    scheduler.create(Schedule("once", OneShotTrigger(at), CallableTarget(lambda: calls.append(1))))
    scheduler.run_pending(datetime.now(UTC))
    scheduler.run_pending(datetime.now(UTC) + timedelta(hours=1))
    assert calls == [1]  # exhausted after first fire


def test_local_pause_prevents_firing_then_resume() -> None:
    scheduler = LocalScheduler()
    calls: list[int] = []
    handle = scheduler.create(
        Schedule("p", IntervalTrigger(1), CallableTarget(lambda: calls.append(1)))
    )
    scheduler.pause(handle)
    assert scheduler.get(handle).status == ScheduleStatus.PAUSED
    scheduler.run_pending(datetime.now(UTC) + timedelta(hours=1))
    assert calls == []
    scheduler.resume(handle)
    scheduler.run_pending(datetime.now(UTC) + timedelta(hours=2))
    assert calls == [1]


def test_local_update_and_delete_and_list() -> None:
    scheduler = LocalScheduler()
    handle = scheduler.create(Schedule("s", IntervalTrigger(10), CallableTarget(lambda: None)))
    assert len(scheduler.list()) == 1
    scheduler.update(handle, Schedule("s", IntervalTrigger(1), CallableTarget(lambda: None)))
    scheduler.delete(handle)
    assert scheduler.list() == []


def test_local_rejects_cron_trigger() -> None:
    scheduler = LocalScheduler()
    with pytest.raises(UnsupportedCapabilityError):
        scheduler.create(Schedule("c", CronTrigger("* * * * *"), CallableTarget(lambda: None)))


def test_local_capabilities() -> None:
    caps = LocalScheduler().capabilities
    assert ScheduleCapability.INTERVAL in caps
    assert ScheduleCapability.ONE_SHOT in caps
    assert ScheduleCapability.CRON not in caps


class TestLocalSchedulerContract(SchedulerContract):
    def make_scheduler(self) -> LocalScheduler:
        return LocalScheduler()

    def sample_schedule(self) -> Schedule:
        return Schedule("c", IntervalTrigger(60), CallableTarget(lambda: None))


# --------------------------------------------------------------------------- #
# Cloud Scheduler (fake client)
# --------------------------------------------------------------------------- #
class FakeSchedulerClient:
    def __init__(self) -> None:
        self.created: list[tuple] = []
        self.deleted: str | None = None

    def create_job(self, parent: str, job: dict) -> SimpleNamespace:
        self.created.append((parent, job))
        return SimpleNamespace(name=job["name"], state=SimpleNamespace(name="ENABLED"))

    def get_job(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, state=SimpleNamespace(name="ENABLED"))

    def pause_job(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, state=SimpleNamespace(name="PAUSED"))

    def resume_job(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, state=SimpleNamespace(name="ENABLED"))

    def update_job(self, job: dict) -> SimpleNamespace:
        return SimpleNamespace(name=job["name"], state=SimpleNamespace(name="ENABLED"))

    def delete_job(self, name: str) -> None:
        self.deleted = name

    def list_jobs(self, parent: str) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(name=f"{parent}/jobs/nightly", state=SimpleNamespace(name="ENABLED"))
        ]


def _gcp(client: FakeSchedulerClient | None = None) -> CloudSchedulerScheduler:
    return CloudSchedulerScheduler(
        project="p", location="us-central1", client=client or FakeSchedulerClient()
    )


def test_gcp_create_builds_cron_http_job() -> None:
    client = FakeSchedulerClient()
    scheduler = _gcp(client)
    handle = scheduler.create(
        Schedule(
            "nightly",
            CronTrigger("0 2 * * *", timezone="Europe/Madrid"),
            HttpTarget("https://svc/_taskport/execute", headers={"X": "1"}),
        )
    )
    assert handle.provider == "gcp"
    _, job = client.created[0]
    assert job["schedule"] == "0 2 * * *"
    assert job["time_zone"] == "Europe/Madrid"
    assert job["http_target"]["uri"].endswith("/_taskport/execute")
    assert job["name"].endswith("/jobs/nightly")


def test_gcp_rejects_interval_trigger() -> None:
    scheduler = _gcp()
    with pytest.raises(UnsupportedCapabilityError):
        scheduler.create(Schedule("i", IntervalTrigger(60), HttpTarget("https://x")))


def test_gcp_pause_resume_delete_list() -> None:
    client = FakeSchedulerClient()
    scheduler = _gcp(client)
    handle = scheduler.create(Schedule("n", CronTrigger("* * * * *"), HttpTarget("https://x")))
    assert scheduler.pause(handle).status == ScheduleStatus.PAUSED
    assert scheduler.resume(handle).status == ScheduleStatus.ENABLED
    assert len(scheduler.list()) == 1
    scheduler.delete(handle)
    assert client.deleted is not None


@pytest.mark.parametrize(
    "state,expected",
    [
        (SimpleNamespace(name="ENABLED"), ScheduleStatus.ENABLED),
        (SimpleNamespace(name="PAUSED"), ScheduleStatus.PAUSED),
        ("ENABLED", ScheduleStatus.ENABLED),
        (None, ScheduleStatus.UNKNOWN),
    ],
)
def test_gcp_state_mapping(state: object, expected: ScheduleStatus) -> None:
    assert map_scheduler_state(state) == expected


class TestCloudSchedulerContract(SchedulerContract):
    def make_scheduler(self) -> CloudSchedulerScheduler:
        return _gcp()

    def sample_schedule(self) -> Schedule:
        return Schedule("c", CronTrigger("0 * * * *"), HttpTarget("https://svc/hook"))
