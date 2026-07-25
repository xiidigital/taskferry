# Deployment profiles

Taskport supports several deployment shapes. Serverless (no permanently-running
worker) is a **first-class** requirement, not an afterthought (section 44).

## Minimal (traditional)

```text
Django ─▶ PostgreSQL ─▶ worker (management command)
```

Backend: Django's built-in `DatabaseBackend` + `db_worker`, or `taskport-django`
with a Procrastinate-backed backend for a more capable PostgreSQL queue. One
long-running worker process. Simplest to operate.

## Redis

```text
Django ─▶ Redis ─▶ worker
```

Backend: a `taskport-django` adapter over a maintained Redis task library
(Dramatiq is the reference integration; Celery/Taskiq are alternatives). One or
more worker processes.

## GCP serverless (scale-to-zero, push)

```text
Django ─▶ Cloud Tasks ─▶ (HTTP push) ─▶ Cloud Run
                Jobs: Cloud Run Jobs
              Events: Pub/Sub
            Schedule: Cloud Scheduler
```

No worker sits polling a queue. Cloud Tasks pushes to a Cloud Run HTTP endpoint;
the service scales to zero when idle. This is a primary target.

## AWS serverless / event-driven

```text
Django ─▶ SQS ─▶ Lambda or ECS/Fargate consumer
           Jobs: AWS Batch on Fargate
         Events: EventBridge / SNS
       Schedule: EventBridge Scheduler
```

SQS is pull-based, so a consumer is required — but it can be a Lambda triggered
by the queue (event-driven, scale-to-zero) rather than a standing worker.

## Azure serverless

```text
Django ─▶ Service Bus ─▶ Azure Functions or Container Apps
           Jobs: Container Apps Jobs
         Events: Event Grid
       Schedule: Logic Apps / Functions timer
```

## Kubernetes

```text
broker ─▶ workers        Jobs: K8s Job        Events: Kafka/NATS        Schedule: CronJob
```

## Choosing

| If you have...                        | Prefer                         |
| ------------------------------------- | ------------------------------ |
| A single VM/container + a database    | Minimal (PostgreSQL)           |
| Existing Redis + workers              | Redis                          |
| GCP and want scale-to-zero            | GCP serverless (push)          |
| AWS and event-driven consumers        | AWS serverless (SQS → Lambda)  |
| Kubernetes                            | Kubernetes profile             |

Taskport lets you start Minimal and move to a serverless profile by swapping the
adapter and configuration — application code (your `@task` functions, `JobSpec`s,
events) does not change.
