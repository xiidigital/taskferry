# Architecture Decision Records

One consolidated sequence, in reading order. Each ADR states one decision, the
alternatives weighed, and the consequences accepted.

| #    | Decision                                                    |
| ---- | ----------------------------------------------------------- |
| 0001 | [Portable execution layer](0001-portable-execution-layer.md) |
| 0002 | [Not a task queue](0002-not-a-task-queue.md)                |
| 0003 | [Task and Job are separate primitives](0003-task-and-job.md) |
| 0004 | [Keep the shared core small](0004-core-scope.md)            |
| 0005 | [Capability model](0005-capability-model.md)                |
| 0006 | [Frameworks are adapters](0006-framework-adapters.md)       |
| 0007 | [Configuration architecture](0007-configuration.md)        |
| 0008 | [Provider-native credentials](0008-provider-credentials.md)|
| 0009 | [Serialization](0009-serialization.md)                     |
| 0010 | [Delivery semantics — never exactly-once](0010-delivery-semantics.md) |
| 0011 | [Retry ownership](0011-retry-ownership.md)                  |
| 0012 | [Observability](0012-observability.md)                     |
| 0013 | [Packaging strategy](0013-packaging-strategy.md)           |
| 0014 | [Monorepo to multi-repo](0014-monorepo-to-multirepo.md)    |
| 0015 | [Async API](0015-async-api.md)                             |

## History — the 0.1 → consolidated renumbering

The 0.1 line grew to 21 ADRs with several "refines"/"supersedes" chains. They were
consolidated into the 15 above: six were folded into the ADR that had already
replaced them, and the rest were renumbered into one coherent order. Historical
references (older commits, external links) map forward as follows.

| Former | Now  | Note                                             |
| ------ | ---- | ------------------------------------------------ |
| 0001   | 0001 | family framing → folded into portable exec layer |
| 0002   | 0003 | separate concepts → folded into Task and Job     |
| 0003   | 0013 | namespace packages → folded into packaging       |
| 0004   | 0004 | core scope (unchanged)                           |
| 0005   | 0005 | capability model → folded into current model     |
| 0006   | 0007 | environment config → folded into configuration   |
| 0007   | 0008 | provider credentials                             |
| 0008   | 0010 | delivery semantics                               |
| 0009   | 0012 | observability                                    |
| 0010   | 0014 | monorepo to multi-repo                           |
| 0011   | 0001 | portable execution layer                         |
| 0012   | 0002 | not a task queue                                 |
| 0013   | 0003 | task vs job                                      |
| 0014   | 0005 | backend capabilities                             |
| 0015   | 0006 | framework adapters                               |
| 0016   | 0007 | configuration architecture                       |
| 0017   | 0009 | serialization                                    |
| 0018   | 0010 | no exactly-once → folded into delivery semantics |
| 0019   | 0011 | retry ownership                                  |
| 0020   | 0013 | packaging strategy                               |
| 0021   | 0015 | async API                                        |
