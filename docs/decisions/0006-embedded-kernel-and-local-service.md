# Decision 0006: one embedded kernel and one local service runtime

Status: accepted
Date: 2026-08-08

## Decision

Crucible implements deterministic canonicalization, validation,
materialization, dependency, and pinned-query mechanics as embeddable,
store-independent kernels. Service V3 composes those kernels with registered
owner handlers, bounded scheduling, durable jobs, cancellation, recovery,
subscriptions, and runtime/session custody. Workbench Shell can host the same
service runtime locally for long-running and multi-client work.

The CLI may invoke the runtime in-process for bounded one-shot operations or
attach to the local host. IDE and other process-isolated clients use a thin
protocol adapter. No client implements alternate graph, authority, profile, or
validation behavior.

The suite-wide service host belongs to Workbench Shell; it aggregates
owner-supplied capabilities and consent while calling registered handlers.
Crucible owns the substrate mechanics and does not import an authority to infer
its meaning. The service owns durable job state, progress, cancellation,
subscriptions, bounded scheduling, and crash recovery; it does not become a
semantic authority or graph repository. Persistence or publication of
handler-produced graph artifacts belongs to an explicit integration layer.

## Reasons

- Tests, CI, and small tools need a simple in-process API.
- Long-running capture and analysis benefit from durable job state and bounded
  writer scheduling.
- Workbench has multiple CLI, IDE, Studio, and automation consumers that must
  receive equivalent results.
- Large worldgen work should be executed once through an owner handler and
  shared by immutable result identity.
- A local-first service preserves the repository's privacy and offline
  guarantees.

## Consequences

- Kernel behavior is transport-independent and has no IDE dependency.
- Workbench stdio framing remains a supported client transport and may proxy
  to the local service runtime.
- Initial shared transport uses an authenticated local operating-system endpoint
  rather than a remotely reachable TCP listener.
- Durable jobs expose structured progress, cancellation, terminal result, and
  failure residue.
- Equivalent requests against the same immutable inputs must return
  semantically equivalent results in-process and out-of-process.
- Service unavailability does not make bounded in-process operation
  impossible. Any handler that mutates local state must still use the runtime's
  one-writer scheduling boundary.

The detailed boundary is defined in
[Crucible runtime kernel and service](../architecture/CRUCIBLE-RUNTIME-SERVICE.md).
