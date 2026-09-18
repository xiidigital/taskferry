"""LocalScheduler — a real in-process scheduler for dev and testing.

Supports interval and one-shot triggers with pause/resume/update/delete. Cron and
timezones are intentionally **not** advertised (a correct cron implementation
belongs to a real scheduler), so a ``CronTrigger`` is rejected loudly per the
capability model rather than half-implemented.

Firing is deterministic via :meth:`run_pending` (pass a ``now`` in tests). An
optional background thread (:meth:`start` / :meth:`stop`) polls in real time.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from taskferry.core import CapabilitySet, ProviderError, ProviderMetadata, new_id

from ..capabilities import ScheduleCapability
from ..errors import ScheduleError, ScheduleNotFoundError
from ..models import FireRecord, Schedule, ScheduleHandle, ScheduleStatus
from ..scheduler import BaseScheduler
from ..targets import CallableTarget, HttpTarget, Target
from ..triggers import IntervalTrigger, OneShotTrigger

_LOCAL_CAPABILITIES = frozenset(
    {
        ScheduleCapability.ONE_SHOT,
        ScheduleCapability.INTERVAL,
        ScheduleCapability.PAUSE,
        ScheduleCapability.RESUME,
        ScheduleCapability.UPDATE,
        ScheduleCapability.DELETE,
    }
)


def _next_run(schedule: Schedule, after: datetime) -> datetime | None:
    trigger = schedule.trigger
    if isinstance(trigger, OneShotTrigger):
        return trigger.at
    if isinstance(trigger, IntervalTrigger):
        return after + timedelta(seconds=trigger.seconds)
    raise ScheduleError(f"LocalScheduler cannot schedule {type(trigger).__name__}")


def _fire_target(target: Target) -> object:
    if isinstance(target, CallableTarget):
        return target.fire()
    if isinstance(target, HttpTarget):
        import urllib.request

        request = urllib.request.Request(
            target.url,
            data=(target.body or "").encode("utf-8") if target.body else None,
            headers=dict(target.headers),
            method=target.method,
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    raise ScheduleError(
        f"LocalScheduler cannot fire target {type(target).__name__}; "
        "use CallableTarget or HttpTarget"
    )


@dataclass
class _Entry:
    schedule: Schedule
    handle: ScheduleHandle
    status: ScheduleStatus
    next_run: datetime | None
    exhausted: bool = False
    fires: list[FireRecord] = field(default_factory=list)


class LocalScheduler(BaseScheduler):
    """In-process scheduler firing interval/one-shot schedules."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def provider(self) -> str:
        return "local"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(_LOCAL_CAPABILITIES, provider="local")

    def _entry(self, handle: ScheduleHandle) -> _Entry:
        with self._lock:
            entry = self._entries.get(handle.id)
        if entry is None:
            raise ScheduleNotFoundError(f"unknown schedule {handle.id!r}")
        return entry

    def _create(self, schedule: Schedule) -> ScheduleHandle:
        schedule_id = new_id("sch")
        status = ScheduleStatus.ENABLED if schedule.enabled else ScheduleStatus.PAUSED
        handle = ScheduleHandle(
            id=schedule_id,
            name=schedule.name,
            status=status,
            provider_metadata=ProviderMetadata(provider="local", provider_id=schedule.name),
        )
        entry = _Entry(
            schedule=schedule,
            handle=handle,
            status=status,
            next_run=_next_run(schedule, datetime.now(UTC)),
        )
        with self._lock:
            self._entries[schedule_id] = entry
        return handle

    def _get(self, handle: ScheduleHandle) -> ScheduleHandle:
        entry = self._entry(handle)
        return entry.handle

    def _list(self) -> list[ScheduleHandle]:
        with self._lock:
            return [entry.handle for entry in self._entries.values()]

    def _set_status(self, handle: ScheduleHandle, status: ScheduleStatus) -> ScheduleHandle:
        entry = self._entry(handle)
        from dataclasses import replace

        entry.status = status
        entry.handle = replace(entry.handle, status=status)
        return entry.handle

    def _pause(self, handle: ScheduleHandle) -> ScheduleHandle:
        return self._set_status(handle, ScheduleStatus.PAUSED)

    def _resume(self, handle: ScheduleHandle) -> ScheduleHandle:
        return self._set_status(handle, ScheduleStatus.ENABLED)

    def _update(self, handle: ScheduleHandle, schedule: Schedule) -> ScheduleHandle:
        entry = self._entry(handle)
        entry.schedule = schedule
        entry.exhausted = False
        entry.next_run = _next_run(schedule, datetime.now(UTC))
        return entry.handle

    def _delete(self, handle: ScheduleHandle) -> None:
        with self._lock:
            self._entries.pop(handle.id, None)

    # -- firing ------------------------------------------------------------- #
    def run_pending(self, now: datetime | None = None) -> list[FireRecord]:
        """Fire every enabled schedule that is due at ``now``. Deterministic."""
        moment = now or datetime.now(UTC)
        fired: list[FireRecord] = []
        with self._lock:
            entries = list(self._entries.values())
        for entry in entries:
            if entry.status is not ScheduleStatus.ENABLED or entry.exhausted:
                continue
            if entry.next_run is None or entry.next_run > moment:
                continue
            try:
                result = _fire_target(entry.schedule.target)
                record = FireRecord(entry.schedule.name, moment, result)
            except ProviderError:
                raise
            except Exception as exc:
                record = FireRecord(entry.schedule.name, moment, exc)
            entry.fires.append(record)
            fired.append(record)
            self._advance(entry, moment)
        return fired

    @staticmethod
    def _advance(entry: _Entry, moment: datetime) -> None:
        trigger = entry.schedule.trigger
        if isinstance(trigger, OneShotTrigger):
            entry.exhausted = True
            entry.next_run = None
        elif isinstance(trigger, IntervalTrigger):
            entry.next_run = moment + timedelta(seconds=trigger.seconds)

    def fires_for(self, handle: ScheduleHandle) -> list[FireRecord]:
        """Return the fire history for a schedule (test/introspection helper)."""
        return list(self._entry(handle).fires)

    # -- optional real-time loop ------------------------------------------- #
    def start(self, poll_interval: float = 1.0) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def _loop() -> None:
            while not self._stop.is_set():
                self.run_pending()
                time.sleep(poll_interval)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def make_local_scheduler(**kwargs: object) -> LocalScheduler:
    return LocalScheduler()


__all__ = ["LocalScheduler", "make_local_scheduler"]
