# taskferry

**A portable execution layer for Python.**

Taskferry models units of work, chooses the right *kind* of execution, and routes
them to engines that already exist. It is not a task queue, not a worker system,
not a scheduler and not a workflow engine.

```bash
pip install taskferry
```

That installs **nothing else**. No Django, no PostgreSQL driver, no Redis client,
no cloud SDK. The distribution declares zero dependencies and CI proves it in a
bare virtualenv on every commit.

```python
from taskferry import Taskferry

runtime = Taskferry.local()


def add(a: int, b: int) -> int:
    return a + b


execution = runtime.inline.submit(add, 20, 22)
assert execution.result().value == 42
```

## Three primitives

```python
runtime.inline.submit(add, 20, 22)  # now, in this process
runtime.tasks.submit("myapp.tasks:send_email", 42)  # later, on an engine
runtime.jobs.submit("build-cog", image="gdal:latest")  # a container, to completion
```

A Job is not "a Task that takes longer": it has its own image, its own resource
envelope and its own lifecycle, and it returns an exit code rather than a Python
value.

## Built-in backends

| Backend | Kind | Use |
| --- | --- | --- |
| `inline` | inline | tests, notebooks, CLIs, small tools |
| `thread` | task | development, single-process applications |
| `process` | task | rehearsing what a real worker does to your code |
| `subprocess` | job | local batch workloads |

None of them is a queue. They are not durable, not visible across processes, and
they lose pending work on restart — which is stated plainly in their docstrings
and in their capability sets. For production, route at a real engine; that is a
configuration change.

## Adapters

Install the engine you use. Each registers itself through the `taskferry.backends`
entry-point group, so naming it in configuration is all it takes.

```bash
pip install taskferry-procrastinate    # PostgreSQL-backed tasks
pip install taskferry-cloudtasks       # push-based serverless tasks
pip install taskferry-cloudrun         # serverless container jobs
pip install taskferry-jobs             # AWS Batch, Kubernetes, Azure Container Apps
pip install taskferry-django           # django.tasks integration
```

## CLI

```bash
taskferry backends
taskferry capabilities pg
taskferry route --kind job --profile gpu
taskferry doctor
```

## Documentation

Full documentation, ADRs and the migration guide live in the
[repository](https://github.com/taskferry/taskferry).

## License

Apache-2.0.
