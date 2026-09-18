"""The wire envelope, re-exported from the core.

Every Taskferry transport carries the same JSON envelope — one function name, JSON
arguments, correlation, retry intent. It lives in :mod:`taskferry.envelope` so
that Procrastinate, Cloud Tasks, SQS, Service Bus and Dramatiq cannot drift apart
on the format a worker has to understand.

This module stays as the import path the adapter's own tests and docs use, and as
the place to put anything genuinely Procrastinate-specific about the payload.
There is currently nothing, which is the point.
"""

from __future__ import annotations

from taskferry.envelope import (
    ENVELOPE_VERSION,
    Envelope,
    build_envelope,
    decode_retry,
    encode_retry,
    read_correlation,
    read_envelope,
)

#: Historical aliases: this adapter shipped them before the envelope moved into
#: the core. Kept so 0.2 adapter code and tests keep working.
MESSAGE_VERSION = ENVELOPE_VERSION
TaskferryMessage = Envelope
build_message = build_envelope


def check_version(message: object) -> None:
    """Reject an envelope this worker does not understand.

    Thin wrapper over :func:`taskferry.envelope.read_envelope` that raises
    ``ValueError`` rather than ``SerializationError``, because a Procrastinate
    worker treats any exception the same way and the narrower type reads better
    in this adapter's own tests.
    """
    from taskferry.errors import SerializationError

    try:
        read_envelope(message)
    except SerializationError as exc:
        raise ValueError(str(exc)) from exc


__all__ = [
    "ENVELOPE_VERSION",
    "MESSAGE_VERSION",
    "Envelope",
    "TaskferryMessage",
    "build_envelope",
    "build_message",
    "check_version",
    "decode_retry",
    "encode_retry",
    "read_correlation",
    "read_envelope",
]
