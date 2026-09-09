# ADR-0012: OpenTelemetry-shaped hooks, but OTel is optional

- Status: Accepted
- Date: 2026-07-24

## Context

Observability must be designed in from the start, but forcing the OpenTelemetry
SDK on every user (and its transitive dependencies) is unacceptable for a
low-level library. We also do not want a separate `taskport-otel` package yet
(no objective reason to split it — section 18).

## Decision

- `taskport-core` defines minimal `Tracer` and `Span` **protocols**, a
  `NoopTracer` default, and centralized **semantic span names**
  (`taskport.task.enqueue`, `taskport.job.submit`, `taskport.event.publish`,
  `taskport.schedule.create`, ...) and **attribute keys**
  (`taskport.provider`, `taskport.task_id`, `taskport.correlation_id`,
  `taskport.attempt`, ...).
- Taskport always **propagates** W3C Trace Context (`traceparent`/`tracestate`)
  and `correlation_id` through `Correlation`, whether or not OTel is installed —
  it just moves the strings.
- OpenTelemetry integration ships as its **own distribution**, `taskport-otel`,
  not a dependency of the core (which declares none, required or optional). The
  bridge implementation stays importable at `taskport.core.otel` and imports
  opentelemetry lazily; installing `taskport-otel` and calling
  `configure_opentelemetry()` activates real spans.

## Consequences

- Zero observability dependencies by default; full tracing when opted in.
- Consistent span/attribute vocabulary across all four domains.
- Revisit a dedicated `taskport-otel` package only if the bridge grows beyond a
  thin adapter.
