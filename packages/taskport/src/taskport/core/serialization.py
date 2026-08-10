"""JSON-only serialization for Taskport payloads (section 23, ADR-0010).

Rules Taskport enforces so payloads stay portable and safe:

* JSON is the default and only built-in transport format.
* **Never** pickle — it is unsafe for cloud/untrusted messages.
* Do not serialize ORM instances or arbitrary Python objects. Pass identifiers
  (``process_resource(resource_id)``), not objects.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from .errors import SerializationError
from .typing import JSONValue


@runtime_checkable
class Serializer(Protocol):
    """Minimal serializer contract adapters can depend on."""

    content_type: str

    def dumps(self, value: JSONValue) -> bytes: ...

    def loads(self, data: bytes | str) -> JSONValue: ...


class JsonSerializer:
    """Strict JSON serializer.

    ``dumps`` rejects non-JSON-serializable input eagerly with
    :class:`SerializationError` instead of producing a partial/garbage payload.
    """

    content_type = "application/json"

    def __init__(self, *, sort_keys: bool = True) -> None:
        self._sort_keys = sort_keys

    def dumps(self, value: JSONValue) -> bytes:
        try:
            text = json.dumps(
                value, sort_keys=self._sort_keys, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError) as exc:
            raise SerializationError(f"value is not JSON-serializable: {exc}") from exc
        return text.encode("utf-8")

    def loads(self, data: bytes | str) -> JSONValue:
        try:
            payload = data.decode("utf-8") if isinstance(data, bytes) else data
            return json.loads(payload)  # type: ignore[no-any-return]
        except (ValueError, UnicodeDecodeError) as exc:
            raise SerializationError(f"payload is not valid JSON: {exc}") from exc


def ensure_json_serializable(value: object) -> JSONValue:
    """Return ``value`` unchanged if it round-trips through JSON, else raise.

    A cheap boundary guard: call it where application data enters Taskport so
    failures surface at enqueue time, not on a remote worker.
    """
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SerializationError(
            f"value of type {type(value).__name__!r} is not JSON-serializable: {exc}"
        ) from exc
    return value  # type: ignore[return-value]


__all__ = [
    "JsonSerializer",
    "Serializer",
    "ensure_json_serializable",
]
