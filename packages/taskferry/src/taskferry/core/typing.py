"""Typing helpers shared across the Taskferry family.

Kept deliberately tiny. Only genuinely cross-cutting aliases live here.
"""

from __future__ import annotations

# JSON is the only transport payload Taskferry promises to serialize (ADR-0010,
# section 23). These PEP 695 aliases document that contract at the type level;
# lazy evaluation lets the recursive definition reference itself without quotes.
type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | JSONArray | JSONObject
type JSONArray = list[JSONValue]
type JSONObject = dict[str, JSONValue]

__all__ = ["JSONArray", "JSONObject", "JSONScalar", "JSONValue"]
