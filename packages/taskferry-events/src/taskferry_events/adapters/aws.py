"""AWS event publishers: SNS (fan-out topics) and EventBridge (event bus).

Both use the default boto3 credential chain (ADR-0008); the client is imported
lazily and can be injected for testing. Event data is serialized as
CloudEvents-structured JSON with portable string attributes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from taskferry.core import (
    CapabilitySet,
    ConfigurationError,
    JsonSerializer,
    ProviderError,
    ProviderMetadata,
)

from ..capabilities import EventCapability
from ..models import Event, PublishResult, event_attributes
from ..publisher import BaseEventPublisher

_SERIALIZER = JsonSerializer()

_SNS_CAPABILITIES = frozenset(
    {
        EventCapability.FANOUT,
        EventCapability.DELIVERY_RETRY,
        EventCapability.DEAD_LETTER,
        EventCapability.FILTERING,
    }
)

_EVENTBRIDGE_CAPABILITIES = frozenset(
    {
        EventCapability.FANOUT,
        EventCapability.DELIVERY_RETRY,
        EventCapability.DEAD_LETTER,
        EventCapability.FILTERING,
        EventCapability.RETENTION,
        EventCapability.REPLAY,
    }
)


class SnsPublisher(BaseEventPublisher):
    """Publishes events to an SNS topic (fan-out to subscribers)."""

    def __init__(
        self,
        *,
        topic_arn: str,
        region: str | None = None,
        client: Any = None,
        async_client: Any = None,
    ) -> None:
        self._topic_arn = topic_arn
        self._region = region
        self._client = client
        self._async_client = async_client
        self._is_fifo = topic_arn.endswith(".fifo")

    @property
    def provider(self) -> str:
        return "aws"

    @property
    def capabilities(self) -> CapabilitySet:
        caps = set(_SNS_CAPABILITIES)
        if self._is_fifo:
            caps.add(EventCapability.ORDERING)
        return CapabilitySet(caps, provider="aws")

    def _sns(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover
                raise ProviderError(
                    "boto3 is required for SnsPublisher; install taskferry-events[aws]",
                    provider="aws",
                ) from exc
            self._client = boto3.client("sns", region_name=self._region)
        return self._client

    def _build_request(self, event: Event) -> dict[str, Any]:
        if not self._topic_arn:
            raise ConfigurationError("SnsPublisher requires a topic_arn")
        attributes = {
            key: {"DataType": "String", "StringValue": value}
            for key, value in event_attributes(event).items()
        }
        request: dict[str, Any] = {
            "TopicArn": self._topic_arn,
            "Message": _SERIALIZER.dumps(event.to_cloudevent()).decode("utf-8"),
            "MessageAttributes": attributes,
        }
        if self._is_fifo:
            request["MessageGroupId"] = event.subject or event.type
        return request

    def _result_from(self, event: Event, response: dict[str, Any]) -> PublishResult:
        metadata = ProviderMetadata(
            provider="aws",
            provider_id=response.get("MessageId"),
            region=self._region,
            resource=self._topic_arn,
        )
        return PublishResult(event_id=event.id, provider_metadata=metadata)

    def _publish(self, event: Event) -> PublishResult:
        request = self._build_request(event)
        try:
            response = self._sns().publish(**request)
        except Exception as exc:
            raise ProviderError(f"sns.publish failed: {exc}", provider="aws") from exc
        return self._result_from(event, response)

    @asynccontextmanager
    async def _async_sns(self) -> AsyncIterator[Any]:
        if self._async_client is not None:
            yield self._async_client
            return
        session = _aiobotocore_session()
        async with session.create_client("sns", region_name=self._region) as client:
            yield client

    async def _apublish(self, event: Event) -> PublishResult:
        if self._async_client is None and not _has_aiobotocore():
            return await super()._apublish(event)
        request = self._build_request(event)
        try:
            async with self._async_sns() as client:
                response = await client.publish(**request)
        except Exception as exc:
            raise ProviderError(f"sns.publish failed: {exc}", provider="aws") from exc
        return self._result_from(event, response)


class EventBridgePublisher(BaseEventPublisher):
    """Publishes events to an Amazon EventBridge event bus."""

    def __init__(
        self,
        *,
        event_bus_name: str = "default",
        region: str | None = None,
        client: Any = None,
        async_client: Any = None,
    ) -> None:
        self._event_bus_name = event_bus_name
        self._region = region
        self._client = client
        self._async_client = async_client

    @property
    def provider(self) -> str:
        return "aws"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(_EVENTBRIDGE_CAPABILITIES, provider="aws")

    def _events(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover
                raise ProviderError(
                    "boto3 is required for EventBridgePublisher; install taskferry-events[aws]",
                    provider="aws",
                ) from exc
            self._client = boto3.client("events", region_name=self._region)
        return self._client

    def _build_entry(self, event: Event) -> dict[str, Any]:
        return {
            "Source": event.source,
            "DetailType": event.type,
            "Detail": _SERIALIZER.dumps(dict(event.data)).decode("utf-8"),
            "EventBusName": self._event_bus_name,
        }

    def _result_from(self, event: Event, response: dict[str, Any]) -> PublishResult:
        if response.get("FailedEntryCount", 0):
            raise ProviderError(
                f"put_events reported failures: {response.get('Entries')}",
                provider="aws",
            )
        entries = response.get("Entries", [{}])
        metadata = ProviderMetadata(
            provider="aws",
            provider_id=entries[0].get("EventId") if entries else None,
            region=self._region,
            resource=self._event_bus_name,
        )
        return PublishResult(event_id=event.id, provider_metadata=metadata)

    def _publish(self, event: Event) -> PublishResult:
        try:
            response = self._events().put_events(Entries=[self._build_entry(event)])
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"put_events failed: {exc}", provider="aws") from exc
        return self._result_from(event, response)

    @asynccontextmanager
    async def _async_events(self) -> AsyncIterator[Any]:
        if self._async_client is not None:
            yield self._async_client
            return
        session = _aiobotocore_session()
        async with session.create_client("events", region_name=self._region) as client:
            yield client

    async def _apublish(self, event: Event) -> PublishResult:
        if self._async_client is None and not _has_aiobotocore():
            return await super()._apublish(event)
        try:
            async with self._async_events() as client:
                response = await client.put_events(Entries=[self._build_entry(event)])
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"put_events failed: {exc}", provider="aws") from exc
        return self._result_from(event, response)


def _has_aiobotocore() -> bool:
    """Whether the async client library is importable, without importing it."""
    from importlib.util import find_spec

    try:
        return find_spec("aiobotocore") is not None
    except ModuleNotFoundError:
        return False


def _aiobotocore_session() -> Any:
    """A lazily-imported aiobotocore session, with an actionable error."""
    try:
        from aiobotocore.session import get_session
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ProviderError(
            "native async AWS events need aiobotocore; install taskferry-events[aws-async]",
            provider="aws",
        ) from exc
    return get_session()


def make_sns_publisher(**kwargs: object) -> SnsPublisher:
    return SnsPublisher(
        topic_arn=str(kwargs["topic_arn"]),
        region=kwargs.get("region"),  # type: ignore[arg-type]
        async_client=kwargs.get("async_client"),
    )


def make_eventbridge_publisher(**kwargs: object) -> EventBridgePublisher:
    return EventBridgePublisher(
        event_bus_name=str(kwargs.get("event_bus_name", "default")),
        region=kwargs.get("region"),  # type: ignore[arg-type]
        async_client=kwargs.get("async_client"),
    )


__all__ = [
    "EventBridgePublisher",
    "SnsPublisher",
    "make_eventbridge_publisher",
    "make_sns_publisher",
]
