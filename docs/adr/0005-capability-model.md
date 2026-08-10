# ADR-0005: Capabilities, not method presence, define what a backend supports

- Status: Accepted
- Date: 2026-07-26
- History: absorbs former ADR-0005 (the first capability model)

## Context

Backends differ in ways that matter. Procrastinate can cancel a queued job and
report its state; Cloud Tasks can do neither. AWS Batch allocates GPUs per
submission; Cloud Run cannot. A portability layer has three options: expose the
lowest common denominator, silently emulate what is missing, or be honest.

0.1 chose honesty but expressed it per-domain: `JobCapability.CANCEL`,
`TaskCapability.CANCELLATION` and `ScheduleCapability.PAUSE` were unrelated types,
so a router or a CLI could not ask "can this backend cancel?" across kinds.

## Decision

One `Capability` enum, shared by every execution kind. Every backend advertises an
immutable `CapabilitySet`, and **that set is the single source of truth** — not
whether a method happens to exist.

Concretely:

- every backend implements the full `submit` / `get` / `cancel` / `result` surface;
- calling an operation whose capability is not advertised raises
  `UnsupportedCapability`, never a no-op and never an emulation;
- a spec declares what it needs via `required_capabilities()`, and `BaseBackend`
  checks it **before** submitting.

```python
runtime.jobs.submit("train", resources=Resources(gpu=1), profile="cloudrun")
# UnsupportedCapability: 'gpu' (provider='cloudrun')
```

## Alternatives considered

**Duck typing — "does the backend have a `.cancel` method?"** Cannot express "can
cancel a queued task but not a running one", cannot be inspected without
instantiating the backend (which may need credentials), and cannot be reported by
`taskport capabilities`. Rejected.

**Optional protocols (`SupportsCancel`, `SupportsResult`).** Types the presence of
methods correctly, but leaves the runtime doing `isinstance` checks against a
growing family of protocols, and still cannot express partial support. Rejected in
favour of one authoritative set.

**Emulate missing capabilities.** An emulated cancel that leaves work running, or
an emulated timeout that does not stop anything, is a correctness bug that only
appears under load in production. Emphatically rejected.

## Consequences

- A capability difference is a loud, immediate, testable failure at submit time.
- The reusable contract suites are capability-driven: a backend that does not
  advertise `CANCEL` is asserted to *reject* cancellation, not skipped.
- Applications that want to be portable across a capability difference must
  feature-detect: `if Capability.CANCEL in handle.capabilities`.
- Some correct-looking code will start raising when a queue is moved to a weaker
  engine. That is the design working: the alternative is it silently doing less.
