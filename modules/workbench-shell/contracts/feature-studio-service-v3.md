# Feature Studio Service V3 composition

Feature Studio Service V3 exposes the embedded owner composition through the
Crucible service runtime. Workbench Shell owns orchestration and local review
binding. Crucible owns context custody, durable jobs, events, subscriptions,
cancellation, terminal seals, and restart recovery.

## Capability set

| Operation | Protocol method | Execution |
| --- | --- | --- |
| `inspect` | `operation/plan` | synchronous, read-only |
| `plan` | `operation/plan` | synchronous, read-only |
| `verify` | `operation/commit` | asynchronous durable job |
| `explain` | `operation/plan` | synchronous, read-only |
| `export` | `operation/commit` | asynchronous durable job |

The service also exposes context registration, successful job-result
retrieval, and Crucible's discovery, cancellation, subscription, and event-page
controls. The live binding projection is
`workbench-feature-studio-service-binding-projection-v3`.

## Context and authority

`context/register` stores one canonical ContextRef V2 and InputBinding V2 pair
after its operation plan and consent binding are checked. Profile state is
copied from the registered owner context; registration does not admit a
profile, create Atlas semantics, admit a Blueprint, replace Crucible custody,
or authorize source application.

## Durable operations

`verify` and `export` require a content-addressed operation plan, explicit
consent, ordered expected context/input heads, and a V3 idempotency key. The
service records the external mutation boundary before entering an owner call
and closes it only after a validated result returns. Failure or cancellation
retains the actual mutation state and never publishes a successful result.

Owner request and result values are retained as canonical JSON with their
SHA-256 digest, byte length, and typed identity. Clients can disconnect,
resume from the last contiguous event cursor, and retrieve the owner result
only after a successful terminal seal. Restarting the same private service
store preserves that durable state.
