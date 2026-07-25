# taskport-core

The tiny shared substrate for the **[Taskport](https://taskport.dev)** family of
portable execution primitives.

> You usually do **not** install this directly. Install `taskport-django`,
> `taskport-jobs`, `taskport-events`, or `taskport-scheduler` — they depend on
> `taskport-core`.

`taskport-core` contains only what is genuinely transversal across Tasks, Jobs,
Events and Schedules, and nothing else (see
[ADR-0004: taskport-core scope](https://taskport.dev/adr/0004)):

- **Identity** — `new_id`, `TaskportId`
- **Correlation** — `Correlation`, `use_correlation`, trace-context propagation
- **Provider metadata** — `ProviderMetadata` (the provider-specific escape hatch)
- **Capability model** — `Capability`, `CapabilitySet`, `UnsupportedCapabilityError`
- **Configuration** — `env_str/int/bool`, `ProviderOptions`, `resolve_factory`
- **Serialization** — `JsonSerializer`, `ensure_json_serializable` (JSON only)
- **Observability** — `Tracer`/`Span` protocols, `NoopTracer`, span/attr names
- **Registry** — `LazyRegistry` (lazy, import-light provider construction)
- **Delivery vocabulary** — `DeliveryGuarantee`, `Ordering`
- **Errors** — `TaskportError` and friends

It has **zero runtime dependencies** and never imports a cloud SDK, Django, or a
broker client.

```bash
pip install taskport-core            # rarely needed on its own
pip install taskport-core[otel]      # OpenTelemetry API for the tracing bridge
```

## Example

```python
from taskport.core import CapabilitySet, Capability, UnsupportedCapabilityError


class JobCapability(Capability):
    CANCEL = "cancel"
    GPU = "gpu"


caps = CapabilitySet({JobCapability.CANCEL}, provider="local")
JobCapability.CANCEL in caps  # True
caps.require(JobCapability.GPU)  # raises UnsupportedCapabilityError
```

## License

Apache-2.0.
