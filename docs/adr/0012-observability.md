# ADR-0012: OpenTelemetry-shaped hooks, but OTel is optional

- Status: Accepted
- Date: 2026-07-24

## Context

Observability is available to every execution kind, but the OpenTelemetry SDK
remains optional so applications that do not need tracing avoid installing it
and its transitive dependencies.

## Decision

- `taskferry` defines minimal `Tracer` and `Span` **protocols**, a
  `NoopTracer` default, and centralized **semantic span names**
  (`taskferry.task.enqueue`, `taskferry.job.submit`, `taskferry.event.publish`,
  `taskferry.schedule.create`, ...) and **attribute keys**
  (`taskferry.provider`, `taskferry.task_id`, `taskferry.correlation_id`,
  `taskferry.attempt`, ...).
- Taskferry always **propagates** W3C Trace Context (`traceparent`/`tracestate`)
  and `correlation_id` through `Correlation`, whether or not OTel is installed —
  it just moves the strings.
- OpenTelemetry integration ships as its **own distribution**, `taskferry-otel`,
  not a dependency of the core (which declares none, required or optional). The
  bridge implementation stays importable at `taskferry.core.otel` and imports
  opentelemetry lazily; installing `taskferry-otel` and calling
  `configure_opentelemetry()` activates real spans.

## Consequences

- Zero observability dependencies by default; full tracing when opted in.
- Consistent span/attribute vocabulary across all four domains.
- Revisit a dedicated `taskferry-otel` package only if the bridge grows beyond a
  thin adapter.
