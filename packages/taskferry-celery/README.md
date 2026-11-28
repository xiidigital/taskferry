# taskferry-celery

A **Celery** task backend for [Taskferry](https://taskferry.dev). Enqueue Taskferry
tasks onto a Celery app you already run; Taskferry supplies no worker and
reimplements none of Celery.

```bash
pip install taskferry-celery[celery]
# Optional Redis transport/result backend support:
pip install taskferry-celery[redis]
# Optional convenience alias when the Celery broker is RabbitMQ:
pip install taskferry-celery[rabbitmq]
```

## Submitting

```python
from taskferry import Taskferry

runtime = Taskferry.from_mapping(
    {
        "backends": {"cel": {"factory": "celery", "app": "myapp.celery:app"}},
        "defaults": {"task": "cel"},
    }
)
runtime.tasks.submit("myapp.tasks:refresh", 42, queue="metadata")
```

## Executing (worker)

Register the dispatcher once, then run Celery's own worker:

```python
from celery import Celery
from taskferry_celery import register_dispatcher
from taskferry.functions import FunctionRegistry

app = Celery("myapp", broker="redis://localhost:6379/0")
# Result storage is independent from the broker and optional.
app.conf.result_backend = "redis://localhost:6379/1"
app.conf.task_serializer = "json"
app.conf.result_serializer = "json"
app.conf.accept_content = ["json"]
register_dispatcher(app, registry=FunctionRegistry(allowed_modules=["myapp"]))
```

RabbitMQ uses Celery's built-in AMQP transport; the `rabbitmq` extra is a
convenience installation alias for Celery and does not install Redis or a
separate RabbitMQ client:

```python
app = Celery(
    "myapp",
    broker="amqp://taskferry:secret@rabbitmq:5672/myvhost",
    backend="rpc://",  # optional result store over AMQP; results are transient
)
```

RabbitMQ as a broker does not by itself store task results. Configure a Celery
result backend separately if `TaskferryBackend.result()` is needed. The RPC
backend can avoid Redis for result delivery; Celery also supports database and
other result backends.

```bash
celery -A myapp worker
```

## Capabilities (honest)

| Advertised | Conditional / unavailable | Why |
| --- | --- | --- |
| submit, state, cancel, delay, priority, retry | `result` only when `app.conf.result_backend` is set | Celery discards return values when result storage is disabled |
| | `deduplication` | Celery has no native mechanism — an `idempotency_key` is rejected, not ignored |

Retries are the **engine's**: the dispatcher raises Celery's own `self.retry`, so
a failed task is genuinely re-queued with the requested countdown. Taskferry never
loops in-process pretending to retry.

The portable `RetryPolicy` counts the first delivery in `max_attempts`; its
backoff, maximum delay, jitter and exception filters are applied per message.
When no policy is declared, the dispatcher requests no retries. Configure one
retry owner: `register_dispatcher()` rejects Celery automatic retry options such
as `autoretry_for` because they would duplicate Taskferry's retry policy. If
`RetryOwner.APPLICATION` is selected, Celery is configured for one attempt and
the application owns subsequent submissions.

`state` maps Celery's `PENDING/STARTED/SUCCESS/FAILURE/RETRY/REVOKED` onto the
portable states. Set Celery's `task_track_started` if workers should persist the
`STARTED` state. Celery reports `PENDING` for both a queued task and an unknown
id, so those remain indistinguishable. `result()` reads Celery's configured
result backend; that can be Redis, a database or another Celery-supported store.
The Redis extra is optional, and Redis is not required as either broker or result
storage. The result store persists Celery's result, but Taskferry's own
Taskferry-id-to-Celery-id index is process-local. For cross-process lookups, keep
the Celery task id (`Execution.external_id`) and pass it to
`runtime.get(celery_id, backend="celery")`; a Django `TaskResult` lookup is not
provided by the bridge.

Delivery is at least once: workers can redeliver after a crash or lost ack. Make
handlers idempotent. Taskferry retry policy is translated to Celery retries, with
Celery as the sole retry owner. Revocation is best effort and cannot stop code
already running. Result backends store a task's final return/error; Taskferry does
not define a provider-neutral progress schema, so persist domain progress in your
application store.

Only JSON-safe arguments and a `"module:function"` name cross the wire — never a
pickled callable.

## License

Apache-2.0.
