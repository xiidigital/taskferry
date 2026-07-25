"""Module-level fake provider clients wired into TASKS settings for tests.

They are singletons so the settings-configured backend instance and the tests
share the same object; tests clear the captured calls as needed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class FakeCloudTasksClient:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def queue_path(self, project: str, location: str, queue: str) -> str:
        return f"projects/{project}/locations/{location}/queues/{queue}"

    def create_task(self, request: dict[str, Any]) -> SimpleNamespace:
        self.created.append(request)
        return SimpleNamespace(name=f"{request['parent']}/tasks/generated-id")

    def reset(self) -> None:
        self.created.clear()


class FakeSQSClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_message(self, **kwargs: Any) -> dict[str, str]:
        self.sent.append(kwargs)
        return {"MessageId": "m-1"}

    def reset(self) -> None:
        self.sent.clear()


CLOUD_TASKS_CLIENT = FakeCloudTasksClient()
SQS_CLIENT = FakeSQSClient()
