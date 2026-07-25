# taskport-django

Portable backends for the **official Django 6 Tasks framework**. Part of the
[Taskport](https://taskport.dev) family.

Taskport does **not** invent a parallel task API. You keep writing standard
Django, and swap the backend via the official `TASKS` setting to move between
local, serverless, and traditional infrastructure — without touching your
`@task` code.

```bash
pip install taskport-django            # local backend
pip install taskport-django[gcp]       # + Cloud Tasks (serverless push)
pip install taskport-django[aws]       # + SQS
```

## Your code never changes

```python
from django.tasks import task


@task
def resize_image(image_id: int) -> None: ...


resize_image.enqueue(42)
```

## Configuration (official `TASKS` setting)

```python
# settings.py — local development
TASKS = {"default": {"BACKEND": "taskport.django.backends.local.LocalBackend"}}
```

```python
# GCP serverless (Cloud Tasks → Cloud Run push)
TASKS = {
    "default": {
        "BACKEND": "taskport.django.backends.cloud_tasks.CloudTasksBackend",
        "OPTIONS": {
            "project": "my-project",
            "location": "us-central1",
            "queue": "default",
            "url": "https://my-service.run.app/_taskport/execute",
            "service_account_email": "runner@my-project.iam.gserviceaccount.com",
        },
    }
}
```

```python
# AWS (SQS → Lambda/ECS consumer)
TASKS = {
    "default": {
        "BACKEND": "taskport.django.backends.sqs.SQSBackend",
        "OPTIONS": {"queue_url": "https://sqs.us-east-1.amazonaws.com/123/my-queue"},
    }
}
```

## Receive side

- **Cloud Tasks (push):** mount the webhook and secure it with platform IAM.
  ```python
  from taskport.django.views import task_webhook

  urlpatterns = [path("_taskport/execute", task_webhook)]
  ```
- **SQS (pull):** run a consumer (Lambda/ECS):
  ```python
  from taskport.django.consumers import process_sqs_event


  def handler(event, context):
      process_sqs_event(event)
  ```

## Backends & capabilities

| Backend             | Provider | Profile             | Notable capabilities                       |
| ------------------- | -------- | ------------------- | ------------------------------------------ |
| `LocalBackend`      | local    | dev (runs inline)   | priority, async, queue selection           |
| `CloudTasksBackend` | gcp      | serverless push     | delay, scheduled execution, retries        |
| `SQSBackend`        | aws      | pull (Lambda/ECS)   | delay (≤900s), dead-letter, FIFO ordering  |

Backends advertise `taskport_capabilities`; unsupported requests raise
`UnsupportedCapabilityError`, and e.g. SQS rejects a `run_after` beyond its
900-second limit rather than silently clamping it.

## Delivery semantics

At-least-once with possible duplicates (ADR-0008). Make your tasks idempotent.

## License

Apache-2.0.
