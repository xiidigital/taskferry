# Multicloud equivalence matrix

These services are **conceptually equivalent**, not identical. Taskferry's
capability model (ADR-0005) exists precisely because their feature sets differ.
Always check `provider.capabilities` rather than assuming parity.

| Capability | GCP                     | AWS                          | Azure                              | Kubernetes            |
| ---------- | ----------------------- | ---------------------------- | ---------------------------------- | --------------------- |
| **Task**   | Cloud Tasks             | SQS                          | Service Bus Queue                  | broker + worker       |
| **Job**    | Cloud Run Jobs          | AWS Batch (+ Fargate)        | Container Apps Jobs                | Job                   |
| **Event**  | Pub/Sub                 | EventBridge / SNS            | Event Grid (or Service Bus Topics) | Kafka / NATS / RabbitMQ |
| **Schedule** | Cloud Scheduler       | EventBridge Scheduler        | Logic Apps / Functions Timer *     | CronJob               |

\* Azure has no single "Cloud Scheduler"-shaped product. Recurring triggering is
assembled from Logic Apps recurrence, Azure Functions timer triggers, or a
Container Apps Job with a cron trigger. Taskferry's Azure scheduler adapter is on
the roadmap for this reason (see [roadmap.md](roadmap.md)).

## Important non-equivalences

- **Cloud Tasks vs SQS.** Cloud Tasks is a *push* system (it calls your HTTP
  endpoint) with native per-task scheduled delivery and rate control. SQS is a
  *pull* queue; scheduled delivery is limited (max 15-minute delay) and a
  consumer (Lambda/ECS) must read it. They are not interchangeable — Taskferry
  exposes the difference through `TaskCapability.SCHEDULED_EXECUTION` and the
  serverless-vs-worker deployment profiles.
- **Cloud Run Jobs vs AWS Batch vs Container Apps Jobs.** All run a container to
  completion, but parallelism, array/indexed execution, GPU support and
  timeout/retry semantics differ. Exposed via `JobCapability.*`.
- **Pub/Sub vs EventBridge vs SNS vs Event Grid.** Ordering, filtering, replay,
  dead-letter and retention differ substantially. Exposed via `EventCapability.*`.

Verify the live service capabilities for your region/tier before relying on any
row above; cloud services change.
