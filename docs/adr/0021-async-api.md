# ADR-0021: A second runtime for async, with the same method names

- Status: Accepted
- Date: 2026-07-27

## Context

Taskport needs to be usable from async code. A Django async view, a FastAPI
handler and a Starlette endpoint all run inside an event loop, and a synchronous
`submit()` blocks it — briefly for Procrastinate's `defer()`, less briefly for a
Cloud Tasks HTTP round-trip, and unacceptably for `handle.wait(300)`.

It also needs to stay usable from code that has **no** loop: a `manage.py`
command, a CLI, a `__main__` script, a notebook. Forcing `asyncio.run()` on those
to enqueue one task would be absurd.

So both surfaces have to exist. The question is what they are called.

## Decision

Two runtime classes, `Taskport` and `AsyncTaskport`, with **identical method
names**. The difference between the two surfaces is `await`, not the vocabulary.

```python
handle = runtime.tasks.submit("myapp.tasks:send_email", 42)
execution = handle.wait(30)

handle = await runtime.tasks.submit("myapp.tasks:send_email", 42)
execution = await handle.wait(30)
```

`AsyncTaskport` **wraps** a sync runtime rather than duplicating it. Routing,
configuration, the backend cache, the execution index, hooks and the function
registry are the same objects, so a process serving both surfaces has one set of
backends and one connection pool.

The adapter-facing port keeps `a`-prefixed names — `asubmit`, `aget`, `acancel`,
`aresult`, `await_` — because there both surfaces sit on **one** object and must
be distinguishable. Adapter authors read those; application developers never do.

`BaseBackend` derives the async methods from the sync ones with
`asyncio.to_thread`. That is a real async path: the caller's loop keeps running.
An adapter with a natively async client overrides them and skips the thread;
correctness does not depend on it doing so.

## Alternatives considered

**`a`-prefixed methods on the single `Taskport` class** — `asubmit`, `aget`,
`aresult`. This is Django's convention (`aenqueue`, `aget_result`) and was the
first choice. It breaks on `wait`: `await` is a keyword, so the prefixed form has
nowhere to go. `await_()` reads as `await handle.await_()`, `awaited()` is past
tense for a future action, and `wait_async()` abandons the convention one method
in. A naming scheme that cannot name one of its five methods is the wrong scheme.

It also makes porting a module between surfaces a rename of every call rather
than adding `await`.

**Async-only, with a sync wrapper** — implement everything async and expose
`asyncio.run()` shims. Rejected: every sync caller would pay loop setup per call,
`asyncio.run` cannot be called from inside a running loop, and a management
command would carry an event loop for no reason.

**Sync-only, and let callers use `asyncio.to_thread`** — what 0.2.0 shipped for
about a day. It works, and it pushes a correctness detail onto every caller: get
it wrong once and an event loop stalls under load, in production, in a way that
looks like a slow database.

**Separate `AsyncTaskBackend` protocols** — a parallel port hierarchy adapters
opt into. Rejected: adapters that did not opt in would have no async path at all,
so the runtime would need a fallback anyway, and application code could not rely
on the async surface existing. Putting the derived default on `BaseBackend`
instead means every adapter has a correct async path from the day it is written.

## Consequences

- `from taskport import AsyncTaskport` is part of the public API.
- Every backend has `asubmit`/`aget`/`acancel`/`aresult`/`await_`, tested by the
  contract suite, and gets a non-blocking implementation for free.
- Two tests keep the "real async path" claim honest, because both would pass
  against a cosmetic implementation: one counts event-loop ticks during a slow
  job, the other requires four 0.2s jobs to finish in ~0.2s rather than ~0.8s.
- There are two classes to keep in step. `_prepare()` and `_owner_backend()` are
  shared verbatim, so routing, correlation and the kind check cannot diverge —
  the places where a silent divergence would be expensive.
- An `async def` task function running on an in-process backend is still executed
  with `asyncio.run` on a worker thread's own loop, because that is what a worker
  in another process would do. See `docs/concurrency.md`.
