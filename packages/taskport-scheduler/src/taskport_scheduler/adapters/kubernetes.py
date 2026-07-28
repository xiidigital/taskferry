"""Kubernetes CronJob scheduler adapter.

A CronJob runs a container on a cron schedule, so the container to run (image,
command, env) comes from ``provider_options["kubernetes"]`` — the escape hatch
(ADR-0006). Only cron triggers apply. Pause/resume map to ``spec.suspend``. The
BatchV1 API client is imported lazily and injectable for testing.
"""

from __future__ import annotations

from typing import Any

from taskport.core import (
    CapabilitySet,
    ConfigurationError,
    ProviderError,
    ProviderMetadata,
    new_id,
)

from ..capabilities import ScheduleCapability
from ..models import Schedule, ScheduleHandle, ScheduleStatus
from ..scheduler import BaseScheduler
from ..triggers import CronTrigger

_K8S_CAPABILITIES = frozenset(
    {
        ScheduleCapability.CRON,
        ScheduleCapability.TIMEZONE,
        ScheduleCapability.PAUSE,
        ScheduleCapability.RESUME,
        ScheduleCapability.UPDATE,
        ScheduleCapability.DELETE,
    }
)


def status_from_suspend(suspend: Any) -> ScheduleStatus:
    """Map a CronJob ``spec.suspend`` flag to portable status. Pure."""
    return ScheduleStatus.PAUSED if bool(suspend) else ScheduleStatus.ENABLED


class KubernetesCronJobScheduler(BaseScheduler):
    """Manages Kubernetes CronJobs."""

    def __init__(self, *, namespace: str = "default", api: Any = None) -> None:
        self._namespace = namespace
        self._api = api

    @property
    def provider(self) -> str:
        return "kubernetes"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(_K8S_CAPABILITIES, provider="kubernetes")

    def _batch(self) -> Any:
        if self._api is None:
            try:
                from kubernetes import client, config
            except ImportError as exc:  # pragma: no cover
                raise ProviderError(
                    "kubernetes is required for KubernetesCronJobScheduler; "
                    "install taskport-scheduler[kubernetes]",
                    provider="kubernetes",
                ) from exc
            try:
                config.load_incluster_config()
            except Exception:
                config.load_kube_config()
            self._api = client.BatchV1Api()
        return self._api

    def _body(self, schedule: Schedule) -> dict[str, Any]:
        trigger = schedule.trigger
        if not isinstance(trigger, CronTrigger):
            raise ProviderError("CronJob requires a CronTrigger", provider="kubernetes")
        opts = schedule.provider_options.for_provider("kubernetes")
        image = opts.get("image")
        if not image:
            raise ConfigurationError(
                "KubernetesCronJobScheduler requires provider_options['kubernetes']"
                "['image'] (the container to run on schedule)"
            )
        container: dict[str, Any] = {"name": schedule.name, "image": image}
        command = opts.get("command")
        if isinstance(command, list):
            container["args"] = [str(part) for part in command]
        env = opts.get("env")
        if isinstance(env, dict):
            container["env"] = [{"name": k, "value": v} for k, v in env.items()]
        spec: dict[str, Any] = {
            "schedule": trigger.expression,
            "suspend": not schedule.enabled,
            "jobTemplate": {
                "spec": {
                    "template": {"spec": {"containers": [container], "restartPolicy": "Never"}}
                }
            },
        }
        if trigger.timezone and trigger.timezone != "UTC":
            spec["timeZone"] = trigger.timezone
        return {
            "apiVersion": "batch/v1",
            "kind": "CronJob",
            "metadata": {"name": schedule.name, "labels": dict(schedule.labels)},
            "spec": spec,
        }

    def _handle(self, name: str, status: ScheduleStatus) -> ScheduleHandle:
        return ScheduleHandle(
            id=new_id("sch"),
            name=name,
            status=status,
            provider_metadata=ProviderMetadata(
                provider="kubernetes",
                provider_id=name,
                resource=f"{self._namespace}/{name}",
            ),
        )

    def _create(self, schedule: Schedule) -> ScheduleHandle:
        body = self._body(schedule)
        try:
            self._batch().create_namespaced_cron_job(namespace=self._namespace, body=body)
        except Exception as exc:
            raise ProviderError(
                f"create_namespaced_cron_job failed: {exc}", provider="kubernetes"
            ) from exc
        return self._handle(schedule.name, status_from_suspend(not schedule.enabled))

    def _read(self, name: str) -> Any:
        try:
            return self._batch().read_namespaced_cron_job(name=name, namespace=self._namespace)
        except Exception as exc:
            raise ProviderError(
                f"read_namespaced_cron_job failed: {exc}", provider="kubernetes"
            ) from exc

    def _get(self, handle: ScheduleHandle) -> ScheduleHandle:
        cron = self._read(handle.name)
        suspend = getattr(getattr(cron, "spec", None), "suspend", False)
        return self._handle(handle.name, status_from_suspend(suspend))

    def _list(self) -> list[ScheduleHandle]:
        try:
            listing = self._batch().list_namespaced_cron_job(namespace=self._namespace)
        except Exception as exc:
            raise ProviderError(
                f"list_namespaced_cron_job failed: {exc}", provider="kubernetes"
            ) from exc
        handles: list[ScheduleHandle] = []
        for item in getattr(listing, "items", []):
            name = getattr(getattr(item, "metadata", None), "name", "")
            suspend = getattr(getattr(item, "spec", None), "suspend", False)
            handles.append(self._handle(name, status_from_suspend(suspend)))
        return handles

    def _patch_suspend(self, name: str, suspend: bool) -> ScheduleHandle:
        try:
            self._batch().patch_namespaced_cron_job(
                name=name, namespace=self._namespace, body={"spec": {"suspend": suspend}}
            )
        except Exception as exc:
            raise ProviderError(
                f"patch_namespaced_cron_job failed: {exc}", provider="kubernetes"
            ) from exc
        return self._handle(name, status_from_suspend(suspend))

    def _pause(self, handle: ScheduleHandle) -> ScheduleHandle:
        return self._patch_suspend(handle.name, True)

    def _resume(self, handle: ScheduleHandle) -> ScheduleHandle:
        return self._patch_suspend(handle.name, False)

    def _update(self, handle: ScheduleHandle, schedule: Schedule) -> ScheduleHandle:
        body = self._body(schedule)
        try:
            self._batch().patch_namespaced_cron_job(
                name=schedule.name, namespace=self._namespace, body=body
            )
        except Exception as exc:
            raise ProviderError(
                f"patch_namespaced_cron_job failed: {exc}", provider="kubernetes"
            ) from exc
        return self._handle(schedule.name, status_from_suspend(not schedule.enabled))

    def _delete(self, handle: ScheduleHandle) -> None:
        try:
            self._batch().delete_namespaced_cron_job(name=handle.name, namespace=self._namespace)
        except Exception as exc:
            raise ProviderError(
                f"delete_namespaced_cron_job failed: {exc}", provider="kubernetes"
            ) from exc


def make_kubernetes_cronjob_scheduler(**kwargs: object) -> KubernetesCronJobScheduler:
    return KubernetesCronJobScheduler(namespace=str(kwargs.get("namespace", "default")))


__all__ = [
    "KubernetesCronJobScheduler",
    "make_kubernetes_cronjob_scheduler",
    "status_from_suspend",
]
