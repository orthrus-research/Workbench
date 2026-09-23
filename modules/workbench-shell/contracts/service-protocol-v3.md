# Workbench service protocol V3

Status: current wire contract with local endpoint and stdio implementations

## Purpose

Service protocol V3 is the transport-neutral JSON-RPC envelope for the one
Workbench application-service path accepted in
[`CRUCIBLE-RUNTIME-SERVICE.md`](../../../docs/architecture/CRUCIBLE-RUNTIME-SERVICE.md).
The local endpoint and stdio proxy translate this envelope without
reimplementing owner, graph, profile, gate, or mutation semantics.

The schema is
[`service-protocol-v3.schema.json`](../schemas/service-protocol-v3.schema.json).
The component and handler inventory is defined by the
[`component capability registry V3`](component-capability-registry-v3.md).

## Protocol and framing

JSON-RPC remains version `2.0`. Workbench protocol V3 is negotiated separately
as major `3`. The first V3 request is `service/initialize`.

Stdio V3 uses the existing byte-correct ASCII framing:

```text
Content-Length: <UTF-8 byte count>\r\n
\r\n
<JSON payload>
```

Shared framing does not make a V2 client a V3 client. Standard output contains
frames only. Logs use standard error. An invalid or ambiguous frame is fatal;
a completely framed invalid JSON value receives JSON-RPC parse error `-32700`.
The negotiated frame limit applies to envelopes, never to the size of a stored
graph or capture.

The initialization result is correlated to the one preceding initialize
request by JSON-RPC ID and Workbench request ID. It repeats the exact protocol
version, active service-distribution and registry IDs, requested feature set,
bounded frame limit, and registry-declared V2 gateway projection. Non-null
JSON-RPC error IDs likewise identify exactly one preceding request; an
unmatched error is invalid rather than a free-standing status report. Across
the complete validated stream there is exactly one initialize request and one
initialize-format result, every JSON-RPC request has exactly one later result
or error, and every non-null result/error ID resolves to exactly one preceding
request. Workbench request-ID uniqueness includes the initialize request and
all domain requests; notification messages remain outside response custody.

## Method families

V3 declares these finite common methods:

| Family | Methods |
| --- | --- |
| `service` | `service/initialize`, `service/capabilities`, `service/health`, `service/shutdown` |
| `context` | `context/list`, `context/get`, `context/resolve`, `context/register` |
| `object` | `object/stat`, `object/read` |
| `evidence` | `evidence/planAdmission`, `evidence/commitAdmission`, `evidence/get`, `evidence/history` |
| `graph` | `graph/materialize`, `graph/get`, `graph/query`, `graph/diff`, `graph/impact`, `graph/subscribe` |
| `operation` | `operation/plan`, `operation/commit` |
| `job` | `job/get`, `job/list`, `job/cancel`, `job/subscribe` |
| `runtime` | `runtime/plan`, `runtime/start`, `runtime/stop`, `runtime/status`, `runtime/instrument` |
| `session` | `session/get`, `session/events`, `session/subscribe`, `session/export` |

Owner-specific work dispatches through a registered capability ID and these
common plan/commit or domain-family handlers. Adding a method name is a protocol
change; it is not an invitation for clients or Shell to grow a semantic switch.

Server notifications are `job/progress` and `subscription/event`. Notifications
have no JSON-RPC ID and never stand in for durable job events or admitted
evidence.

## Request binding

Every non-handshake request binds:

- protocol version and a transport request ID;
- exact content-addressed capability ID and semantic version;
- an exact `context_ref_id` and `input_binding_id`, with null allowed only for
  descriptor-declared service or context-discovery methods;
- operation intent and the descriptor's exact V3 operation class;
- closed parameters validated by the descriptor's request schema;
- declared resource budget and optional deadline; and
- plan, consent, expected-head, and idempotency bindings where required.

Before dispatch, a mandatory context/input applicability port receives a frozen
request containing the exact canonical request and capability bytes plus the
typed ContextRef/InputBinding IDs. It validates the lane-A pair and the
descriptor-owned applicability rule. A syntactically valid content ID is not an
admitted context, and this protocol does not create a weaker second context
admission path. `context/resolve` applies the same port to the exact resolved
pair and response projection.

Selectors are closed owner-schema-validated `arguments` to `context/resolve`,
never envelope identity. Resolution yields the canonical
`context-ref:sha256:...` and `input-binding:sha256:...` records owned by the C02
context contract. A client cannot send a workspace label, pack name, seed, or
world path in a field that claims to be an exact context ID.

Unknown fields, undeclared methods, descriptor drift, ambiguous versions, and
parameters that do not satisfy the exact per-method owner request schema fail
before dispatch. Direct result values are likewise checked against the exact
registered result schema before transport. A transport request ID, idempotency
key, job ID, plan ID, context ID, and revision ID are distinct and cannot
substitute for one another.

## Response binding and outcomes

Every domain response repeats the capability and handler identities actually
used. Its JSON-RPC ID, Workbench request ID, protocol/capability versions, and
context/input bindings correlate with exactly one request; duplicate Workbench
request IDs fail before dispatch. A successful `context/resolve` correlates
both the newly materialized ContextRef and InputBinding from its direct result.
Every response also includes:

- exact resolved `context_ref_id` and `input_binding_id`;
- exact input and output revision IDs;
- typed platform/pack profile revision references and support-decision IDs when
  applicable;
- separate implementation availability, profile support, evidence,
  completeness, and action-gate state plus exact gate-decision IDs when a gate
  policy applies;
- ordered diagnostics and limitations; and
- exactly one direct result, durable job handle, or structured domain failure.

The input-binding ID commits to the exact evidence, graph, graph-set, recipe,
schema, ontology, adapter, and index revisions used. Responses also expose the
relevant revision IDs directly so a client need not infer them from labels or a
moving ref.

Profile support, action-gate, and job projections use separate mandatory
validation ports. Each receives an explicit role and immutable canonical
request, response, and capability projections. Only exact boolean `true`
accepts; false, another truthy type, a missing port, or an exception fails closed
with a stable diagnostic. Before each call, the shell recursively reconstructs
a detached, exact-type request, including nested mappings and sequences. A port
therefore receives no authoritative protocol object, and force-mutation of its
request is checked after return and rejected with a stable port-mutation
diagnostic. Support decisions cannot authorize an action gate.

Transport success is not domain success. JSON-RPC `error` is reserved for
transport, envelope, version, method, and parameter failures. Domain outcomes
use a normal JSON-RPC result with one of:

- `succeeded` with a descriptor-schema-validated result and bounded object
  references;
- `accepted` with a durable `job-v2:...` handle and exact job-submission ID;
- `unavailable` when an implementation, context, input, or profile decision is
  not available;
- `blocked` when consent, gate, lease, freshness, or another declared policy
  prevents work; or
- `failed` for a completed failed attempt with exact residue when present.

An unavailable or blocked result is truthful and actionable; it is never
collapsed into an empty success.

Knowledge uncertainty is not service unavailability. A graph query or
inspection may succeed with `unresolved` or `conflicted` completeness when its
typed result preserves the frontier, conflict, diagnostics, and exact input
revisions. `unavailable` is reserved for a missing required implementation,
context, input, authority decision, or comparable prerequisite.

## Durable jobs, progress, and cancellation

A job handle contains the operational `job_id`, content-addressed
`job_submission_id`, latest durable event ID and ordinal when known, and
optional terminal-seal ID. Mutable status projections are rebuildable hints,
not authority.

Every accepted handle is validated through the lane-B job projection port
against the exact submission, context/input pair, capability and handler,
idempotency, plan and consent, durable event head, and optional seal. The same
port validates progress event IDs/ordinals and every job-bearing failure. A
typed-looking job ID or seal is not sufficient, and response output-revision
membership must agree with the exact job submission.

`job/progress` references an exact durable job event. It carries the job ID,
event ID and monotonic ordinal, phase, completed work, nullable total, unit, and
whether mutation has begun. A missing total stays null. Progress can be
coalesced in delivery but may not fabricate ordinals or evidence.

`job/cancel` binds the expected event ID and ordinal, requester, and reason.
Cancellation is an append request, not rollback. The terminal job seal reports
`cancelled-before-mutation`, `cancelled-after-mutation`, or `indeterminate`
according to the durable mutation state. Completed job-domain failures repeat
the exact mutation state and terminal-seal content ID. Pre-dispatch failures
carry null job, mutation, and seal fields with `mutation_possible=false`;
later failures derive that flag from the exact lane-B mutation state.
Cancellation after immutable publication cannot retract the revision.
Every `failed` outcome with a non-null job is completed and therefore requires
a non-null terminal seal, including budget, deadline, and internal failures.

## Resumable subscriptions

Graph, job, and session subscription requests name exact event families and a
closed, non-context-wide scope: exact graph/graph-set/reference revisions, an
exact job plus submission, or an exact runtime-session record plus operational
session ID. They then select one of:

- `start` for a new stream; or
- `resume` with the prior subscription ID, stream generation, and last
  contiguous cursor.

Every handle and event repeats the exact context and subscription scope. Every
event also repeats subscription ID, generation, and the next contiguous cursor,
then references immutable revision, ref-event, job-event, terminal-seal,
session, or retained object IDs. Resuming with a different scope, context,
generation, event family, or delivery contract is a replay error. The service
never resumes silently from `now`.
Validation replays the actual stream in order from the original start handle,
including exact InputBinding, delivered cursors, and declared dropped ranges.
A resume cursor equals the last contiguous observed cursor. Duplicate start
handles for one subscription ID are rejected rather than resolved by
last-writer-wins selection.

If continuity cannot be proven, the result is `subscription-gap` and includes
the minimum available cursor plus a revision-resynchronization instruction.
Lossy live events are permitted only when the capability descriptor declares
them and the event reports exact dropped cursor ranges.

## Bounded object references and reads

Large values are returned as object references containing exact object ID,
media type, optional schema ID, byte length, privacy class, and digest. They are
never embedded merely because the transport frame permits it.

`object/read` accepts an exact object ID and a bounded byte range. V3.0 limits a
single range to 1 MiB; negotiation may lower but not silently raise this limit.
The result repeats validated object metadata, returned offset and length,
base64 bytes, EOF state, and the next offset when more bytes remain. A path is
never an object identity, and this method cannot read arbitrary host files.
An EOF chunk may be empty. A non-EOF chunk has positive length and advances
`next_offset`; returning the same offset indefinitely is invalid.

Canonical record-page continuations may be added only through a descriptor and
schema that bind the exact object/revision, algorithm, filters, order, page
budget, and frontier. They are not process-memory cursors.

## Structured failures

The stable failure kinds are:

`invalid-request`, `unsupported-version`, `unavailable-capability`,
`degraded-capability`, `unknown-context`, `stale-context`,
`incompatible-scope`, `unsupported-profile`, `gate-blocked`,
`consent-required`, `stale-plan`, `freshness-failed`, `identity-mismatch`,
`validation-failed`, `quarantined`, `admission-rejected`, `writer-busy`,
`lease-uncertain`, `compare-and-swap-lost`, `budget-exceeded`, `backpressure`,
`deadline-exceeded`, `cancel-requested`, `cancelled-before-mutation`,
`cancelled-after-mutation`, `indeterminate-mutation`, `subscription-gap`,
`continuation-invalid`, `corrupt-object`, `stale-index`, `missing-input`, and
`internal-failure`.

Every failure states retryability, exact nullable mutation state, and whether
mutation may have occurred. It contains ordered diagnostics, remediation
actions, exact nullable job and terminal-seal IDs, residue, and minimum-cursor
bindings. The schema mechanically partitions failure kind, disposition,
retryability, job/seal requirements, and mutation-state reduction. Free-form
text cannot carry those facts.

## Ownership boundary

Workbench Shell owns authenticated local hosting, session negotiation, framing,
capability projection, consent presentation, and transport forwarding. It does
not own graph interpretation, profile support, Blueprint approval, Manuals
authorization, or an alternate mutation route.

The capability owner and profile adapters decide semantics through their public
handlers. The service runtime pins context and inputs, schedules jobs, and
returns the same structured result in-process or over either transport. The
stdio proxy owns no store lease or semantic state.

## Conformance boundary

The retained vectors and transport-independent validator in
`workbench_shell.service_contract` prove schema closure and representative V3 binding for
initialization, direct results, durable jobs, progress, cancellation,
subscriptions, gaps, object ranges, unavailable and blocked states, and the V2
gateway separation. The validator returns stable diagnostics for registry
composition and correlated message streams; it contains no listener, stdio
loop, handler dispatch, owner semantics, or service implementation. Applicable
profile responses fail closed unless the caller supplies the profile-adapter
port that validates the exact support-decision IDs against their profile
revisions.
The public message validator consumes an exact canonical snapshot of actual
message objects and an explicit candidate index. Conformance case IDs and
`{case_id,value}` substitution exist only in tests. All other messages in the
correlated stream are schema checked, and custom containers, forbidden numeric
types, invalid indexes, callback failures, and internal validation exceptions
produce stable diagnostics rather than escaping.

Before a V3 transport is implemented, further executable tests must prove
bounded framing, authentication, exact descriptor dispatch, context isolation,
job recovery, cancellation mutation semantics, contiguous subscription resume,
large-object streaming, and semantic parity with the embedded handler.
