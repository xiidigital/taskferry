"""AWS EventBridge Scheduler adapter.

EventBridge Scheduler fires at cron/rate/one-time expressions and targets AWS
resources by ARN (not arbitrary HTTP), so the target ARN + execution role come
from ``provider_options["aws"]`` — the provider-specific escape hatch (ADR-0006).
The boto3 client is imported lazily and injectable for testing.
"""

from __future__ import annotations

from typing import Any

from taskport.core import (
    CapabilitySet,
    ConfigurationError,
    JsonSerializer,
    ProviderError,
    ProviderMetadata,
    new_id,
)

from ..capabilities import ScheduleCapability
from ..models import Schedule, ScheduleHandle, ScheduleStatus
from ..scheduler import BaseScheduler
from ..triggers import CronTrigger, IntervalTrigger, OneShotTrigger

_SERIALIZER = JsonSerializer()

_AWS_CAPABILITIES = frozenset(
    {
        ScheduleCapability.CRON,
        ScheduleCapability.INTERVAL,
        ScheduleCapability.ONE_SHOT,
        ScheduleCapability.TIMEZONE,
        ScheduleCapability.PAUSE,
        ScheduleCapability.RESUME,
        ScheduleCapability.UPDATE,
        ScheduleCapability.DELETE,
    }
)

# Keys accepted by update_schedule, used to round-trip a fetched definition.
_UPDATE_KEYS = (
    "Name",
    "ScheduleExpression",
    "ScheduleExpressionTimezone",
    "Target",
    "FlexibleTimeWindow",
    "State",
    "GroupName",
    "Description",
)


def build_schedule_expression(schedule: Schedule) -> tuple[str, str | None]:
    """Map a trigger to an EventBridge Scheduler expression + timezone. Pure."""
    trigger = schedule.trigger
    if isinstance(trigger, CronTrigger):
        return f"cron({trigger.expression})", trigger.timezone
    if isinstance(trigger, IntervalTrigger):
        minutes = max(1, round(trigger.seconds / 60))
        return f"rate({minutes} minutes)", None
    if isinstance(trigger, OneShotTrigger):
        # EventBridge one-time schedules use at(yyyy-mm-ddThh:mm:ss).
        return f"at({trigger.at.strftime('%Y-%m-%dT%H:%M:%S')})", None
    raise ProviderError(f"unsupported trigger {type(trigger).__name__}", provider="aws")


class EventBridgeScheduler(BaseScheduler):
    """Manages AWS EventBridge Scheduler schedules."""

    def __init__(self, *, region: str | None = None, client: Any = None) -> None:
        self._region = region
        self._client = client

    @property
    def provider(self) -> str:
        return "aws"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(_AWS_CAPABILITIES, provider="aws")

    def _scheduler(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover
                raise ProviderError(
                    "boto3 is required for EventBridgeScheduler; install taskport-scheduler[aws]",
                    provider="aws",
                ) from exc
            self._client = boto3.client("scheduler", region_name=self._region)
        return self._client

    def _target(self, schedule: Schedule) -> dict[str, Any]:
        opts = schedule.provider_options.for_provider("aws")
        arn = opts.get("target_arn")
        role_arn = opts.get("role_arn")
        if not arn or not role_arn:
            raise ConfigurationError(
                "EventBridgeScheduler requires provider_options['aws'] with "
                "'target_arn' and 'role_arn' (targets are AWS ARNs, not HTTP)"
            )
        target: dict[str, Any] = {"Arn": str(arn), "RoleArn": str(role_arn)}
        payload = opts.get("input")
        if payload is not None:
            target["Input"] = (
                payload if isinstance(payload, str) else _SERIALIZER.dumps(payload).decode("utf-8")
            )
        return target

    def _params(self, schedule: Schedule) -> dict[str, Any]:
        expression, timezone = build_schedule_expression(schedule)
        params: dict[str, Any] = {
            "Name": schedule.name,
            "ScheduleExpression": expression,
            "Target": self._target(schedule),
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "State": "ENABLED" if schedule.enabled else "DISABLED",
            "Description": schedule.description,
        }
        if timezone:
            params["ScheduleExpressionTimezone"] = timezone
        return params

    def _handle(self, name: str, state: str, arn: str | None = None) -> ScheduleHandle:
        status = ScheduleStatus.ENABLED if state == "ENABLED" else ScheduleStatus.PAUSED
        return ScheduleHandle(
            id=new_id("sch"),
            name=name,
            status=status,
            provider_metadata=ProviderMetadata(
                provider="aws", provider_id=arn or name, region=self._region
            ),
        )

    def _create(self, schedule: Schedule) -> ScheduleHandle:
        params = self._params(schedule)
        try:
            response = self._scheduler().create_schedule(**params)
        except Exception as exc:
            raise ProviderError(f"create_schedule failed: {exc}", provider="aws") from exc
        return self._handle(schedule.name, params["State"], response.get("ScheduleArn"))

    def _get(self, handle: ScheduleHandle) -> ScheduleHandle:
        definition = self._fetch(handle.name)
        return self._handle(handle.name, definition.get("State", ""), definition.get("Arn"))

    def _list(self) -> list[ScheduleHandle]:
        try:
            response = self._scheduler().list_schedules()
        except Exception as exc:
            raise ProviderError(f"list_schedules failed: {exc}", provider="aws") from exc
        return [
            self._handle(item["Name"], item.get("State", ""), item.get("Arn"))
            for item in response.get("Schedules", [])
        ]

    def _fetch(self, name: str) -> dict[str, Any]:
        try:
            return dict(self._scheduler().get_schedule(Name=name))
        except Exception as exc:
            raise ProviderError(f"get_schedule failed: {exc}", provider="aws") from exc

    def _set_state(self, name: str, state: str) -> ScheduleHandle:
        definition = self._fetch(name)
        params = {k: definition[k] for k in _UPDATE_KEYS if k in definition}
        params["Name"] = name
        params["State"] = state
        try:
            self._scheduler().update_schedule(**params)
        except Exception as exc:
            raise ProviderError(f"update_schedule failed: {exc}", provider="aws") from exc
        return self._handle(name, state, definition.get("Arn"))

    def _pause(self, handle: ScheduleHandle) -> ScheduleHandle:
        return self._set_state(handle.name, "DISABLED")

    def _resume(self, handle: ScheduleHandle) -> ScheduleHandle:
        return self._set_state(handle.name, "ENABLED")

    def _update(self, handle: ScheduleHandle, schedule: Schedule) -> ScheduleHandle:
        params = self._params(schedule)
        try:
            self._scheduler().update_schedule(**params)
        except Exception as exc:
            raise ProviderError(f"update_schedule failed: {exc}", provider="aws") from exc
        return self._handle(schedule.name, params["State"])

    def _delete(self, handle: ScheduleHandle) -> None:
        try:
            self._scheduler().delete_schedule(Name=handle.name)
        except Exception as exc:
            raise ProviderError(f"delete_schedule failed: {exc}", provider="aws") from exc


def make_eventbridge_scheduler(**kwargs: object) -> EventBridgeScheduler:
    return EventBridgeScheduler(region=kwargs.get("region"))  # type: ignore[arg-type]


__all__ = [
    "EventBridgeScheduler",
    "build_schedule_expression",
    "make_eventbridge_scheduler",
]
