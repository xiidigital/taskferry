"""Deterministic local scheduling with run_pending.

uv run python examples/scheduler-local/run.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from taskferry_scheduler import (
    CallableTarget,
    IntervalTrigger,
    LocalScheduler,
    Schedule,
)


def main() -> None:
    scheduler = LocalScheduler()
    ticks: list[str] = []

    scheduler.create(
        Schedule(
            name="heartbeat",
            trigger=IntervalTrigger(seconds=60),
            target=CallableTarget(lambda: ticks.append("tick")),
        )
    )

    # Simulate time passing deterministically (no real waiting).
    now = datetime.now(UTC)
    for minute in range(1, 4):
        scheduler.run_pending(now + timedelta(minutes=minute))

    print("fired", len(ticks), "times ->", ticks)


if __name__ == "__main__":
    main()
