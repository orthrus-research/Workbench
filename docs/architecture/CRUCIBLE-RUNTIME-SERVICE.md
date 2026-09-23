# Crucible runtime and local service

Status: current public architecture; protocol and runtime major 3

## Purpose

Crucible provides Workbench's transport-independent mechanics for immutable
records, bounded graph computation, runtime observation, and durable local
jobs. Workbench Shell hosts those mechanics for CLI and IDE clients. The
embedded, local endpoint, and stdio paths all dispatch through the same Service
V3 runtime.

Crucible owns custody and deterministic mechanics; it does not acquire the
meaning owned by Atlas, the construction authority owned by Blueprints, or the
support policy owned by a selected profile.

This architecture applies the [Crucible graph model](CRUCIBLE-GRAPH-MODEL.md)
and [Decision 0006](../decisions/0006-embedded-kernel-and-local-service.md).

## Supported implementation

There is one supported service generation:

- `workbench_crucible_service` implements dispatch, one-writer scheduling,
  exact context custody, durable jobs, cancellation, recovery, subscriptions,
  and bounded query leases;
- `workbench_core.service.host` implements authentication, protocol
  negotiation, local endpoint hosting, and stdio adaptation; and
- registered owner handlers implement domain meaning and policy.

The `v2` suffixes on retained graph, context, and job packages identify stable
record formats used by the service. They are not alternate service editions.

## Invariants

1. Embedded and hosted calls reach the same registered handlers.
2. Every semantic operation binds an exact `ContextRef` and `InputBinding`
   before work begins.
3. One writer owns a configured store; readers lease immutable revisions.
4. Durable job history is append-only. Mutable indexes and status rows are
   rebuildable projections.
5. Owner handlers keep evidence admission, artifact production, and external
   mutation as distinct boundaries.
6. Implementation health, evidence quality, profile support, and action
   authorization remain separate response states.
7. Large objects move by validated reference and bounded reads rather than
   unbounded protocol messages.
8. Runtime state, captures, credentials, caches, and logs stay in ignored
   `.workbench/` storage.

## Authority boundary

| Concern | Owner |
| --- | --- |
| Canonical bytes, structural validation, immutable graph mechanics, pinned reads, and job custody | Crucible |
| Mechanical interpretation, recipes, causal semantics, and query meaning | Atlas |
| Construction plans and approval meaning | Blueprints |
| Platform and pack support policy | Selected profile |
| Capability aggregation, consent routing, hosting, and client projection | Workbench Shell |
| Display and interaction | CLI and IDE clients |

The host aggregates capabilities; it does not implement a second graph engine,
profile resolver, validation path, or approval path.

Service V3 is a handler and job runtime, not a graph repository. A registered
handler may consume or produce immutable graph artifacts, but the service does
not add a publication-proof workflow, moving-reference store, or index.

## Runtime shape

```text
CLI / IDE clients
        |
embedded, local endpoint, or stdio transport
        |
Workbench Shell Service V3 host
        |
Crucible Service V3 runtime
        |
registered Atlas / Blueprints / profile handlers
        |
caller-supplied immutable data and ignored local runtime state
```

The service runtime validates registrations, resolves exact contexts and
inputs, schedules work, records durable job events, and returns structured
results. Owner handlers are explicit registrations; directory scanning and
filename inference are not capability discovery mechanisms.

## Context and input binding

Workspace names, profile names, runtime labels, and moving references are
selectors. The service resolves them to immutable context and input records
before dispatch. Plans and terminal results retain those exact identities.
Missing scope is never borrowed from a neighboring workspace or profile.

The closed shapes are defined by the
[ContextRef and InputBinding contract](../../modules/crucible/contracts/crucible-context-and-input-binding-v2.md).

## Jobs, storage, and recovery

Service V3 records queued work, attempts, progress, cancellation, and terminal
outcomes as append-only job records. Recovery validates that history rather
than inferring success from a process disappearing or a mutable status file.

Generated state is separated by trust class under `.workbench/`, including
service leases, job state, managed runtimes, captures, and worlds. Handler-owned
immutable artifacts and disposable indexes, when present, follow the same
boundary. None of that state belongs in a source release.

Writer ownership fails closed when a lease is live or uncertain. Readers pin
immutable revisions, so handler-owned state can advance without changing an
in-flight read. Expensive work happens outside the serialized mutation section;
a mutating handler revalidates its own expected state before committing.

Cancellation is an append-only request, not erasure. If external effects may
have begun, the terminal result reports the reconciled or indeterminate state
instead of claiming a clean cancellation.

## Transport and privacy

The protocol is JSON-RPC 2.0 with Workbench protocol major 3. Local endpoints
are owner-authenticated and do not listen on a remotely reachable interface by
default. Stdio uses byte-counted framing and carries protocol frames on standard
output; logs use standard error.

Credentials never enter Git, arguments, receipts, logs, graph objects, or
telemetry. The service performs no implicit upload, telemetry, update, or
network fetch. Content identity proves byte equality, not permission to share
data between workspaces or users.

## Failure behavior

- Invalid registrations, contexts, inputs, or payloads fail before owner work.
- A live or uncertain competing writer blocks mutation.
- A stale plan or expected head loses compare-and-swap without overwriting the
  accepted result.
- Cancellation after effects begin requires reconciliation.
- Missing or corrupt immutable objects invalidate the referencing job or
  revision.
- Resource exhaustion or capture loss produces an explicit failure or
  incomplete result, never a false success.
- An unsupported profile at a protected gate returns a structured blocked
  result.

## Public entry points

Discover current Crucible actions with:

```bash
workbench capabilities crucible
```

The active protocol and record contracts are:

- [Service protocol V3](../../modules/workbench-shell/contracts/service-protocol-v3.md)
- [ContextRef and InputBinding V2](../../modules/crucible/contracts/crucible-context-and-input-binding-v2.md)
- [durable job and runtime-session records V2](../../modules/crucible/contracts/crucible-durable-job-session-record-family-v2.md)
- [bounded computed graph materializer V2](../../modules/crucible/contracts/crucible-bounded-computed-graph-materializer-v2.md)
- [deterministic graph shard kernel V2](../../modules/crucible/contracts/crucible-deterministic-graph-shard-kernel-v2.md)
- [revision-pinned query kernel V2](../../modules/crucible/contracts/crucible-revision-pinned-query-kernel-v2.md)

Run Crucible conformance tests from the repository root:

```bash
python3 -m unittest discover -s modules/crucible/tests -p 'test_*.py'
```
