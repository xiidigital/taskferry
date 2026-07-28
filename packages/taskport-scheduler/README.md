# taskport-scheduler

Portable **"when to fire"** triggering. Ships alongside [Taskport](https://github.com/taskport/taskport),
the portable execution layer. Plain Python, no Django required.

A **Schedule** decides *when*; it never runs business logic itself. It triggers
another capability — a Task, a Job, or an Event — by composition.

```bash
pip install taskport-scheduler            # in-process scheduler
pip install taskport-scheduler[gcp]       # + Google Cloud Scheduler
```

## Usage

```python
from taskport_scheduler import Schedule, CronTrigger, HttpTarget, schedulers

schedulers.configure(
    {
        "default": {
            "factory": "taskport_scheduler.adapters.gcp:make_cloud_scheduler",
            "project": "my-proj",
            "location": "us-central1",
        },
    }
)

schedulers["default"].create(
    Schedule(
        name="nightly-report",
        trigger=CronTrigger("0 2 * * *", timezone="Europe/Madrid"),
        # Fire a Task by hitting your Django task webhook (composition):
        target=HttpTarget("https://my-service.run.app/_taskport/execute"),
    )
)
```

Local development / testing (in-process, deterministic):

```python
from taskport_scheduler import LocalScheduler, Schedule, IntervalTrigger, CallableTarget
from datetime import datetime, timedelta, UTC

s = LocalScheduler()
s.create(Schedule("beat", IntervalTrigger(seconds=60), CallableTarget(lambda: print("tick"))))
s.run_pending(datetime.now(UTC) + timedelta(minutes=2))  # deterministic firing
# or s.start()  # background thread for real-time firing
```

## Triggers, targets, capabilities

- **Triggers:** `CronTrigger`, `IntervalTrigger`, `OneShotTrigger`.
- **Targets:** `HttpTarget` (fire a Task/service), `PubSubTarget` (fire an Event),
  `CallableTarget` (in-process, local only).

Schedulers differ, and the capability set says so honestly:

| Adapter                       | Provider   | Capabilities                                      |
| ----------------------------- | ---------- | ------------------------------------------------- |
| `LocalScheduler`              | local      | one_shot, interval, pause, resume, update, delete |
| `CloudSchedulerScheduler`     | gcp        | cron, timezone, pause, resume, update, delete     |
| `EventBridgeScheduler`        | aws        | cron, interval, one_shot, timezone, pause, resume, update, delete |
| `KubernetesCronJobScheduler`  | kubernetes | cron, timezone, pause, resume, update, delete     |

`LocalScheduler` rejects a `CronTrigger` and Cloud Scheduler / CronJob reject an
`IntervalTrigger` with `UnsupportedCapabilityError` — no silent misbehavior.

Roadmap: Azure scheduling (no single equivalent — Logic Apps / Functions timer).

## Composition, not orchestration

A schedule fires *one* capability. Taskport does not provide DAG workflows or
state machines — that is out of scope (see the family composition docs).

## License

Apache-2.0.
