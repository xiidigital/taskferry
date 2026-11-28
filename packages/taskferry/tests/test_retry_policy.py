from __future__ import annotations

from taskferry import Backoff, RetryOwner, RetryPolicy, TaskSpec
from taskferry.envelope import build_envelope, decode_retry


def test_backoff_delays_use_attempt_numbers_and_apply_ceiling() -> None:
    policy = RetryPolicy(
        max_attempts=5,
        backoff=Backoff.EXPONENTIAL,
        initial_delay=2,
        max_delay=5,
    )

    assert [policy.delay_for(attempt) for attempt in range(1, 6)] == [0, 2, 4, 5, 5]


def test_exception_filters_match_short_and_fully_qualified_names() -> None:
    policy = RetryPolicy(max_attempts=3, retry_on=("builtins.ConnectionError",))

    assert policy.should_retry(ConnectionError("temporary"), attempt=1)
    assert not policy.should_retry(ValueError("permanent"), attempt=1)
    assert not policy.should_retry(ConnectionError("last"), attempt=3)


def test_no_retry_filter_takes_precedence_over_retry_filter() -> None:
    policy = RetryPolicy(
        max_attempts=3,
        retry_on=("Exception",),
        no_retry_on=("ValueError",),
    )

    assert not policy.should_retry(ValueError(), attempt=1)
    assert policy.should_retry(RuntimeError(), attempt=1)


def test_owner_and_jitter_survive_the_json_envelope_round_trip() -> None:
    policy = RetryPolicy(
        max_attempts=3,
        jitter=False,
        owner=RetryOwner.APPLICATION,
    )
    envelope = build_envelope(TaskSpec(task="tests:run", retry=policy))

    restored = decode_retry(envelope)

    assert restored.owner is RetryOwner.APPLICATION
    assert restored.jitter is False
    assert restored.engine_attempts == 1
    assert not restored.should_retry(RuntimeError(), attempt=1)


def test_old_envelopes_default_to_backend_owned_jittered_retry() -> None:
    restored = decode_retry(
        {
            "taskferry": "2",
            "task": "tests:run",
            "retry": {"max_attempts": 3},
        }
    )

    assert restored.owner is RetryOwner.BACKEND
    assert restored.jitter is True


def test_exception_filter_acknowledgement_only_applies_before_attempt_limit() -> None:
    policy = RetryPolicy(max_attempts=2, retry_on=("ConnectionError",))

    assert policy.filtered_out(ValueError(), attempt=1)
    assert not policy.filtered_out(ValueError(), attempt=2)
