"""CloudSchedulerScheduler — manage schedules on Google Cloud Scheduler.

Cloud Scheduler is cron-only with timezone support (no interval/one-shot), which
the capability set states honestly. The Google SDK is imported lazily and the
client can be injected for testing (ADR-0008).
"""

from __future__ import annotations

from typing import Any

from taskport.core import (
    CapabilitySet,
    ProviderError,
    ProviderMetadata,
    new_id,
)

from ..capabilities import ScheduleCapability
from ..errors import ScheduleError
from ..models import Schedule, ScheduleHandle, ScheduleStatus
from ..scheduler import BaseScheduler
from ..targets import HttpTarget, PubSubTarget
from ..triggers import CronTrigger

_GCP_CAPABILITIES = frozenset(
    {
        ScheduleCapability.CRON,
        ScheduleCapability.TIMEZONE,
        ScheduleCapability.PAUSE,
        ScheduleCapability.RESUME,
        ScheduleCapability.UPDATE,
        ScheduleCapability.DELETE,
    }
)


def map_scheduler_state(state: Any) -> ScheduleStatus:
    """Map a Cloud Scheduler ``Job.State`` (or duck-typed value) to portable status."""
    text = str(getattr(state, "name", state) or "").upper()
    if "PAUSED" in text:
        return ScheduleStatus.PAUSED
    if "ENABLED" in text:
        return ScheduleStatus.ENABLED
    return ScheduleStatus.UNKNOWN


class CloudSchedulerScheduler(BaseScheduler):
    """Manages Cloud Scheduler jobs."""

    def __init__(self, *, project: str, location: str, client: Any = None) -> None:
        self._project = project
        self._location = location
        self._client = client

    @property
    def provider(self) -> str:
        return "gcp"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(_GCP_CAPABILITIES, provider="gcp")

    def _scheduler(self) -> Any:
        if self._client is None:
            try:
                from google.cloud import scheduler_v1
            except ImportError as exc:  # pragma: no cover
                raise ProviderError(
                    "google-cloud-scheduler is required for CloudSchedulerScheduler; "
                    "install taskport-scheduler[gcp]",
                    provider="gcp",
                ) from exc
            self._client = scheduler_v1.CloudSchedulerClient()
        return self._client

    def _parent(self) -> str:
        return f"projects/{self._project}/locations/{self._location}"

    def _job_name(self, name: str) -> str:
        return f"{self._parent()}/jobs/{name}"

    def _build_job(self, schedule: Schedule) -> dict[str, Any]:
        trigger = schedule.trigger
        if not isinstance(trigger, CronTrigger):
            raise ScheduleError("Cloud Scheduler requires a CronTrigger")
        job: dict[str, Any] = {
            "name": self._job_name(schedule.name),
            "schedule": trigger.expression,
            "time_zone": trigger.timezone,
            "description": schedule.description,
        }
        target = schedule.target
        if isinstance(target, HttpTarget):
            job["http_target"] = {
                "uri": target.url,
                "http_method": target.method,
                "headers": dict(target.headers),
                "body": (target.body or "").encode("utf-8"),
            }
        elif isinstance(target, PubSubTarget):
            import json

            job["pubsub_target"] = {
                "topic_name": target.topic,
                "data": json.dumps(target.data).encode("utf-8"),
                "attributes": dict(target.attributes),
            }
        else:
            raise ScheduleError("Cloud Scheduler supports HttpTarget or PubSubTarget only")
        return job

    def _handle(self, name: str, job: Any) -> ScheduleHandle:
        return ScheduleHandle(
            id=new_id("sch"),
            name=name,
            status=map_scheduler_state(getattr(job, "state", None)),
            provider_metadata=ProviderMetadata(
                provider="gcp",
                provider_id=getattr(job, "name", self._job_name(name)),
                region=self._location,
            ),
        )

    def _create(self, schedule: Schedule) -> ScheduleHandle:
        job = self._build_job(schedule)
        try:
            created = self._scheduler().create_job(parent=self._parent(), job=job)
        except Exception as exc:
            raise ProviderError(f"create_job failed: {exc}", provider="gcp") from exc
        return self._handle(schedule.name, created)

    def _get(self, handle: ScheduleHandle) -> ScheduleHandle:
        name = handle.provider_metadata.provider_id or self._job_name(handle.name)
        try:
            job = self._scheduler().get_job(name=name)
        except Exception as exc:
            raise ProviderError(f"get_job failed: {exc}", provider="gcp") from exc
        return self._handle(handle.name, job)

    def _list(self) -> list[ScheduleHandle]:
        try:
            jobs = self._scheduler().list_jobs(parent=self._parent())
        except Exception as exc:
            raise ProviderError(f"list_jobs failed: {exc}", provider="gcp") from exc
        handles: list[ScheduleHandle] = []
        for job in jobs:
            name = str(getattr(job, "name", "")).rsplit("/", 1)[-1]
            handles.append(self._handle(name, job))
        return handles

    def _pause(self, handle: ScheduleHandle) -> ScheduleHandle:
        name = handle.provider_metadata.provider_id or self._job_name(handle.name)
        try:
            job = self._scheduler().pause_job(name=name)
        except Exception as exc:
            raise ProviderError(f"pause_job failed: {exc}", provider="gcp") from exc
        return self._handle(handle.name, job)

    def _resume(self, handle: ScheduleHandle) -> ScheduleHandle:
        name = handle.provider_metadata.provider_id or self._job_name(handle.name)
        try:
            job = self._scheduler().resume_job(name=name)
        except Exception as exc:
            raise ProviderError(f"resume_job failed: {exc}", provider="gcp") from exc
        return self._handle(handle.name, job)

    def _update(self, handle: ScheduleHandle, schedule: Schedule) -> ScheduleHandle:
        job = self._build_job(schedule)
        try:
            updated = self._scheduler().update_job(job=job)
        except Exception as exc:
            raise ProviderError(f"update_job failed: {exc}", provider="gcp") from exc
        return self._handle(schedule.name, updated)

    def _delete(self, handle: ScheduleHandle) -> None:
        name = handle.provider_metadata.provider_id or self._job_name(handle.name)
        try:
            self._scheduler().delete_job(name=name)
        except Exception as exc:
            raise ProviderError(f"delete_job failed: {exc}", provider="gcp") from exc


def make_cloud_scheduler(**kwargs: object) -> CloudSchedulerScheduler:
    return CloudSchedulerScheduler(
        project=str(kwargs["project"]),
        location=str(kwargs["location"]),
    )


__all__ = ["CloudSchedulerScheduler", "make_cloud_scheduler", "map_scheduler_state"]
