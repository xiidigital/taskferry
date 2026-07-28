# ADR-0017: JSON payloads and named functions; never pickle

- Status: Accepted
- Date: 2026-07-26
- Refines: [ADR-0008](0008-delivery-semantics.md)

## Context

A task that runs elsewhere needs two things to cross the boundary: a way to say
*which function*, and a way to carry *its arguments*. Python's obvious answer to
both is `pickle`.

## Decision

**Functions travel as names.** The portable form is `package.module:function` —
unambiguous (a colon separates module from attribute, so `a.b.c` never has to be
guessed at), already the convention used by entry points, uvicorn and gunicorn,
and readable as a plain string in any transport.

**Arguments travel as JSON**, validated eagerly when the spec is constructed, so a
bad payload fails in the caller's stack trace rather than on a worker an hour
later.

Serializers are pluggable via the `Serializer` protocol, but JSON is the only
built-in and the only one a backend may assume.

Inline execution is the deliberate exception: it carries a live callable, because
there is no boundary to cross. That is exactly why lambdas, closures and notebook
functions work inline and are rejected for tasks — with an error that says why.

## Alternatives considered

**Pickle callables and arguments.** It is what several task queues do, and it
would let users enqueue closures and ORM instances. Rejected on three counts:

1. **Security.** Unpickling attacker-controlled bytes is arbitrary code execution.
   A queue is a trust boundary; a Cloud Tasks endpoint is a public one.
2. **Deployability.** A pickled payload ties the producer and the consumer to one
   interpreter version and one code layout. Rolling deploys break.
3. **Debuggability.** `SELECT * FROM procrastinate_jobs` should be readable.

**Pickle as an opt-in serializer.** Rejected: shipping it makes it the path of
least resistance for exactly the users least equipped to evaluate the risk.

**A binary format (msgpack, protobuf) as the default.** Faster and more compact,
and neither is the bottleneck for a queue payload. JSON is inspectable by every
tool an operator already has. Rejected for the default; the protocol allows it.

## Consequences

- Pass identifiers, not objects: `process_dataset(dataset_id)`, never
  `process_dataset(dataset)`. This is stated in the docs and enforced by eager
  validation.
- A worker in another process, another container or another language can execute
  a Taskport message.
- Resolving a name means importing a module, which is an RCE primitive if names
  arrive from an untrusted queue. `FunctionRegistry` therefore supports explicit
  registration and an import allowlist. See [security.md](../security.md).
