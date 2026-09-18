# taskferry-dramatiq

Run [Taskferry](https://github.com/taskferry/taskferry) tasks on
[Dramatiq](https://dramatiq.io) — Redis or RabbitMQ, with real worker processes.

```mermaid
flowchart LR
    APP["Application"] --> TP["Taskferry"] --> AD["taskferry-dramatiq"] --> DQ["Dramatiq"] --> B["Redis / RabbitMQ"] --> W["dramatiq worker"]
```

```bash
pip install 'taskferry-dramatiq[redis]'
```

Dramatiq is the Redis-shaped counterpart to Procrastinate's PostgreSQL: a real
broker, real workers, native retries with exponential backoff and jitter, and a
dead-letter queue. Taskferry reimplements none of it.

## Sending

```python
runtime = Taskferry.from_mapping(
    {
        "backends": {"dq": {"factory": "dramatiq", "actor": "myapp.worker:taskferry_execute"}},
        "defaults": {"task": "dq"},
    }
)

runtime.tasks.submit("myapp.tasks:send_email", 42, queue="email")
```

## Executing

Build the dispatch actor once, in the module your worker loads:

```python
# myapp/worker.py
import dramatiq
from dramatiq.brokers.redis import RedisBroker
from taskferry import FunctionRegistry
from taskferry_dramatiq import build_dispatch_actor

dramatiq.set_broker(RedisBroker(url="redis://localhost:6379"))

taskferry_execute = build_dispatch_actor(
    registry=FunctionRegistry(allowed_modules=["myapp"]),  # task names come off the broker
    max_retries=3,
)
```

```bash
dramatiq myapp.worker
```

Taskferry supplies no worker. `dramatiq` is the worker, and it is a good one.

## Capabilities, next to Procrastinate

| | Procrastinate | Dramatiq |
| --- | :---: | :---: |
| `SUBMIT` | yes | yes |
| `DELAY` | yes | yes |
| `RETRY` | yes | yes |
| `PRIORITY` | yes | **no** |
| `DEDUPLICATION` | yes | **no** |
| `STATE` | yes | **no** |
| `CANCEL` | yes | **no** |
| `RESULT` | **no** | **no** |

Dramatiq's default brokers keep no queryable record of one message, so there is
nothing to look up and nothing to revoke. A handle refuses `status()` rather than
returning a plausible `UNKNOWN` forever.

Dramatiq *does* offer a results middleware. `RESULT` is still not advertised,
because whether it works depends on middleware Taskferry cannot see from here —
and a capability that is true only sometimes is worse than one honestly absent.

## License

Apache-2.0.
