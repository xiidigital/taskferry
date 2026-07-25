# Changelog — taskport-events

Follows [Semantic Versioning](https://semver.org/); versions independently of the
rest of the family (ADR-0010).

## [Unreleased]

## [0.1.0]

### Added

- `Event` model (CloudEvents-inspired: id, type, source, subject, time, data),
  `PublishResult`, `event_attributes`.
- `EventPublisher` port + `BaseEventPublisher` (tracing).
- `EventCapability` capability vocabulary.
- `InMemoryEventBus` — real in-process fan-out with type/predicate filtering,
  replay and retention.
- `PubSubPublisher` (GCP) reference adapter with lazy SDK import and injectable
  client.
- `publishers` lazy registry (in-memory bus registered as `"default"`).
- `EventPublisherContract` reusable contract test suite.
- Import-lightness guarantee and `py.typed`.
