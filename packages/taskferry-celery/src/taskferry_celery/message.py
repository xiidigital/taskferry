"""The wire envelope, re-exported from the core.

Every Taskferry transport carries the same JSON envelope — one function name, JSON
arguments, correlation, retry intent — defined once in :mod:`taskferry.envelope`
so Celery, Procrastinate, Cloud Tasks, SQS and the rest cannot drift apart on the
format a worker must understand. There is nothing Celery-specific to add, which is
the point; this module exists as the stable import path for the adapter's own
tests and docs.
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

MESSAGE_VERSION = ENVELOPE_VERSION
TaskferryMessage = Envelope
build_message = build_envelope


def check_version(message: object) -> None:
    """Reject an envelope this worker does not understand, as ``ValueError``."""
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
