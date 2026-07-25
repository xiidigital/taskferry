"""Tests for portable message building, resolution and consumer-side execution."""

from __future__ import annotations

import json

import pytest

import tasks
from taskport.core import Correlation
from taskport.django import build_message, execute_task_message, resolve_task
from taskport.django.consumers import process_sqs_event, process_sqs_message


def test_build_and_resolve_roundtrip() -> None:
    message = build_message(tasks.add, (2, 3), {})
    assert message["module_path"] == "tasks.add"
    assert message["args"] == [2, 3]
    # JSON-serializable end to end.
    assert json.loads(json.dumps(message))["name"] == "add"
    assert resolve_task(message) is tasks.add


def test_build_message_rejects_non_json_args() -> None:
    from taskport.core import SerializationError

    with pytest.raises(SerializationError):
        build_message(tasks.record, (object(),), {})


def test_execute_runs_task_and_returns_value() -> None:
    tasks.CALLS.clear()
    message = build_message(tasks.add, (10, 5), {})
    assert execute_task_message(message) == 15
    assert tasks.CALLS == [15]


def test_execute_unknown_task_raises_lookup() -> None:
    with pytest.raises(LookupError):
        execute_task_message({"module_path": "tasks.nope", "name": "nope"})


def test_execute_propagates_correlation() -> None:
    # The executed task records the correlation bound during its run.
    tasks.SEEN_CORRELATIONS.clear()
    corr = Correlation.start()
    message = build_message(tasks.capture_correlation, ("x",), {}, correlation=corr)
    execute_task_message(message)
    assert [corr.correlation_id] == tasks.SEEN_CORRELATIONS


def test_sqs_consumer_helpers() -> None:
    tasks.CALLS.clear()
    body = json.dumps(build_message(tasks.record, ("a",), {}))
    assert process_sqs_message(body) == "a"
    event = {"Records": [{"body": json.dumps(build_message(tasks.record, ("b",), {}))}]}
    assert process_sqs_event(event) == ["b"]
    assert tasks.CALLS == ["a", "b"]
