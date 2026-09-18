# taskferry-celery

A **Celery** task backend for [Taskferry](https://taskferry.dev). Enqueue Taskferry
tasks onto a Celery app you already run; Taskferry supplies no worker and
reimplements none of Celery.

```bash
pip install taskferry-celery[celery]
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
register_dispatcher(app, registry=FunctionRegistry(allowed_modules=["myapp"]))
```

```bash
celery -A myapp worker
```

## Capabilities (honest)

| Advertised | Not advertised | Why not |
| --- | --- | --- |
| submit, state, cancel, delay, priority, retry | `result` | needs a configured result backend Taskferry can't see |
| | `deduplication` | Celery has no native mechanism — an `idempotency_key` is rejected, not ignored |

Retries are the **engine's**: the dispatcher raises Celery's own `self.retry`, so
a failed task is genuinely re-queued with the requested countdown. Taskferry never
loops in-process pretending to retry.

`state` maps Celery's `PENDING/STARTED/SUCCESS/FAILURE/RETRY/REVOKED` onto the
portable states. Note Celery reports `PENDING` for both a queued task and an
unknown id; without a result backend the two are indistinguishable, and this
adapter says so rather than guessing.

Only JSON-safe arguments and a `"module:function"` name cross the wire — never a
pickled callable.

## License

Apache-2.0.
