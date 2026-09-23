# Crucible durable job and runtime-session record family V2

Status: normative implementation contract. This additive family does not
reinterpret a V1 identity or extend the immutable-graph record registry.

## Purpose and authority boundary

This family records what a durable Crucible job requested, which immutable
inputs it used, which attempts and processes owned its work, what append-only
runtime facts occurred, and how the history was terminally sealed. It does not
make mutable queue rows, worker heartbeats, status projections, lock tables, or
lease expiry calculations authoritative. Those values are disposable indexes
derived from the immutable records defined here plus current observations.

Workbench orchestrates these facts. ContextRef and InputBinding identify the
authoritative context and inputs; handlers and their owning applications remain
responsible for domain meaning, admission, and protected mutations. A job event
is operational provenance, never Atlas evidence by itself.

The family deliberately specifies records and a finite publication validator,
not a daemon, scheduler, database, queue, or lease store.

## Canonical domain and identifiers

All six records use `workbench-canonical-json-v2`, schema version `2`, and the
C01 canonical JSON byte and content-identity algorithms. Each top-level `id` is
recomputed with `record_content_id`; it is not an operational handle.

The closed record family is:

| Kind | Format | Schema |
| --- | --- | --- |
| `job-submission` | `workbench-crucible-job-submission-v2` | `workbench://schemas/crucible/crucible-job-submission-v2.schema.json` |
| `job-attempt` | `workbench-crucible-job-attempt-v2` | `workbench://schemas/crucible/crucible-job-attempt-v2.schema.json` |
| `runtime-session` | `workbench-crucible-runtime-session-v2` | `workbench://schemas/crucible/crucible-runtime-session-v2.schema.json` |
| `process-identity` | `workbench-crucible-process-identity-v2` | `workbench://schemas/crucible/crucible-process-identity-v2.schema.json` |
| `job-event` | `workbench-crucible-job-event-v2` | `workbench://schemas/crucible/crucible-job-event-v2.schema.json` |
| `job-terminal-seal` | `workbench-crucible-job-terminal-seal-v2` | `workbench://schemas/crucible/crucible-job-terminal-seal-v2.schema.json` |

`job_id`, `attempt_id`, `session_id`, `process_instance_id`, resource-claim IDs,
cancellation-request IDs, recovery IDs, and service-instance IDs are random
operational identities. Their prefixes and entropy domains are fixed by
`crucible-job-v2-common.schema.json`. They must never be substituted for the
content IDs above. In particular, a PID is not a process identity.

Every record binds both:

- an exact `context_ref_id` conforming to
  `crucible-context-ref-v2.schema.json#/$defs/contextRefId`; and
- an exact `input_binding_id` conforming to
  `crucible-input-binding-v2.schema.json#/$defs/inputBindingId`.

The publication validator resolves both records and requires
`InputBinding.context_ref_id == record.context_ref_id`. Job records do not copy
workspace, profile, runtime, world, dimension, or other ContextRef fields.
Schema and content-ID validity are not sufficient authority. Publication also
requires a fail-closed lane-A context-publication port over the exact canonical
ContextRef and InputBinding bytes. That port attests lane-A support, workspace
binding, ancestry, and input applicability; the job validator cannot create a
weaker second ContextRef admission path.

Every other typed plan, consent, policy, implementation, descriptor, decision,
and observation reference is presented to a required application-owned
applicability port as the exact path, referenced kind and ID, containing record
ID **and canonical bytes**, job ID, ContextRef ID, and InputBinding ID. The
canonical bytes are the complete immutable record projection; an adapter must
not reconstruct it through an unstated secondary lookup. Only literal `true`
accepts the projection. Missing ports, exceptions, and replay under another
containing record or modified record projection fail closed. Semantic text is
never scanned for reference-shaped values.

Both authority ports are untrusted callback boundaries. For every invocation,
the job validator constructs a detached request from pinned primitive IDs and
canonical bytes; it never exposes an authoritative record wrapper or mutable
record projection. After the callback returns, the validator rechecks the
request's exact type, field types, and pre-call metadata. Forced mutation of a
nominally frozen request rejects publication even when the callback returns
`true`, and the isolated mutation cannot alter later validation.

## Record relations

`job-submission` is the immutable job anchor. It binds the capability, handler,
implementation, exact request descriptor, optional plan and consent, an exact
idempotency digest/scope/policy, requested resource classes, mutation boundary,
submitter, privacy, and retention. `evidence_eligible` is always false.
The capability applicability attestation covers the **whole submission**: the
capability/registry descriptor must authorize the exact handler and
implementation, request contract, mutation boundary, requested claims, plan,
consent, and idempotency policy in those canonical containing-record bytes.
Workbench never treats those caller-supplied fields as an independent approval.

`job-attempt` gives one execution attempt a random `attempt_id` and a zero-based
ordinal. Ordinal zero has no predecessor. Each later attempt points to the
immediately preceding attempt record, increments its ordinal by one, and keeps
the job, ContextRef, and InputBinding unchanged. Its claim keys must be unique
and drawn from the submission request.

A job cancelled while still queued can have zero attempts. Therefore a terminal
seal's attempt array is closed and complete but is not required to be nonempty.
An attempt repeats the submitted handler implementation. Attempt zero is
`initial` with no retry decision; every later ordinal has a non-initial reason,
an exact retry decision, and the immediately prior attempt record. A non-null
attempt `session_id` identifies exactly one locally supplied session owned by
that attempt.

When `retry_of_terminal_seal_id` is present, the prior seal and submission are
resolved. The retry has a distinct job identity, uses `retry-after-terminal`,
and preserves ContextRef, InputBinding, capability, handler, implementation,
request descriptor, idempotency digest, and idempotency scope.

`runtime-session` is the immutable prelaunch/raw-custody anchor for an attempt.
It binds one exact attempt record and may bind a parent session from the same
job, context, and inputs. It is operational custody, not admitted evidence.

`process-identity` prevents PID reuse from creating false ownership. Its
identity tuple includes platform, host instance, boot, PID, process-start token,
executable, argv, environment, working directory, and its exact binding proof.
It must bind one exact job attempt and runtime session. `launched` requires a
`process-launch-observation` and a null adoption proof. An adopted process uses
`adopted-after-exact-reconciliation`, a null launch observation, and an exact
`process-adoption-reconciliation` record. Its applicability attestation covers
the complete process tuple, owner chain, reconciliation decision, and canonical
containing-record bytes; a matching PID alone never authorizes adoption.
Within one validated publication, the OS tuple `(platform, host_instance_id,
boot_id, pid, process_start_token)` is globally unique across jobs as well as
within a job. One exact process cannot acquire multiple content identities by
being rebound to different job anchors.

`job-event` is one append-only fact in a job-local singly linked ledger. It
always carries nullable attempt/session/process pairs, so absence is explicit.
For each pair, both the record content ID and random operational ID are null or
both are present; when present they must resolve to the same job, context,
inputs, and owner chain. Every event has `evidence_eligible:false`.

`job-terminal-seal` is the immutable declaration that a complete event prefix
has one terminal meaning. It binds the exact terminal event, head, count, event
root, all attempts/sessions/processes in the sealed prefix, resource accounting,
recovery obligations, diagnostics, limitations, outcome, mutation state, and
result or failure. At most one logical terminal seal may occur for a `job_id` in
a validated publication. A distinct second content ID is still a conflicting
second seal, not a new status version.

A complete seal is closed, not merely resolvable: every attempt/session/process
owned by an event, every session parent, every process supervisor, and every
orphan/recovery process reference reachable from the sealed prefix is supplied
locally and enumerated by the seal. Active partial validation may use a pinned
resolver, but ancestry walks are capped at 64 records and cannot be called
complete.

## Append-only event ledger

Events are ordered by `event_ordinal`, not arrival time. The first event has
ordinal `0`, type `queued`, and `previous_event_id:null`. Each subsequent event
has ordinal `n`, points to the event at `n-1`, and preserves job, submission,
ContextRef, and InputBinding. An event with lifecycle `terminal` has type
`terminal-ready` and is the final event in the sealed prefix. No event may be
appended after it.

For the chronological array `E = [event_0.id, ..., event_n.id]`, the exact
ledger root is:

```text
content_id(
  "job-event-ledger-root-v2",
  {"event_ids": E, "job_id": job_id}
)
```

`content_id` is the C01 domain-separated content algorithm and the object is
encoded with canonical JSON V2. There is no concatenation or implementation
defined ordering in this formula.

Lifecycle transitions are restricted to:

```text
queued     -> queued | starting | orphaned | terminal
starting   -> starting | running | recovering | orphaned | terminal
running    -> running | recovering | retrying | orphaned | terminal
recovering -> recovering | running | retrying | orphaned | terminal
retrying   -> retrying | starting | orphaned | terminal
orphaned   -> orphaned | recovering
terminal   -> (none)
```

The event discriminator and its closed `body` discriminator must agree. Event
type also constrains the resulting lifecycle: `queued`, `attempt-starting`,
`running`, `recovery-classified`, `retrying`, `orphaned`, and `terminal-ready`
produce their named lifecycle phases; resource/progress/cancellation/mutation
and process facts preserve a mechanically valid surrounding phase.
More exactly, resource, progress, cancellation, mutation, process-bind, and
process-exit facts repeat the prior lifecycle. Only explicit phase events
advance it. Only `mutation-state-changed` or a mechanically coupled
`recovery-classified` event may change mutation state; every other event repeats
the prior state.

Attempt event order is authoritative. Attempt ordinal zero is initially
authorized. A `retrying` event is owned by the exact predecessor attempt and
binds the next attempt's content ID, operational attempt ID, ordinal, and retry
decision. It must occur before the later attempt's first event. Each attempt has
exactly one `attempt-starting` event, and no other attempt-owned event may
precede it. Existence elsewhere in the sealed prefix is insufficient.
The retry event retires its predecessor for new work. Once the next attempt
starts it becomes the only active execution attempt. A retired attempt may emit
only late cleanup/reconciliation facts: `resource-claim-released`,
`process-exited`, `orphaned`, `recovery-classified`, `cancellation-requested`,
or `cancellation-observed`. It cannot emit running, progress, mutation, new
claim, process-bind, retry, or terminal facts.
Likewise, `process-bound` is the first event owned by a process identity; no
process-owned observation, including `process-exited`, may precede it. A process
has at most one bind event and at most one exit event in a ledger prefix.
After exit, that process may own only `resource-claim-released`, `orphaned`,
`recovery-classified`, cancellation request/observation, or `terminal-ready`
cleanup facts. It cannot return to running, progress, mutation, acquisition,
binding, or other new work.

A retry decision creates an exact authorized next-attempt record before that
attempt starts. Cancellation may be requested and observed in this scheduling
window. A terminal seal may therefore enumerate exactly one unstarted attempt
only when it is the highest ordinal, its exact predecessor `retrying` event is
in the sealed prefix, it emitted no event, and the terminal outcome is a
cancelled outcome. Every other enumerated attempt has exactly one
`attempt-starting` event. This is an explicit scheduled-but-never-started
disposition; a false start event is never synthesized to satisfy custody.

## Progress, cancellation, resources, and mutation

Progress is a durable operational observation, not a replaceable percentage.
For one attempt, phase, and unit, `completed` cannot decrease; a non-null total
cannot change and completed cannot exceed it. `mutation_started` is false for
`not-started` and `temporary-residue` and true for every other state. The
indeterminate external state is deliberately treated as crossed rather than
optimistically safe. A text value that happens to resemble a content ID remains
text.

Cancellation intent and cancellation observation are separate event kinds. A
request binds the immediately preceding event ID and ordinal as an optimistic
head check. An observation binds one exact request event and request ID. A
cancelled terminal outcome requires an observed request; merely writing a
cancellation request never proves that a handler stopped.

Resource acquisition and release are ledger facts. `lease_authoritative:false`
states that `expires_at` is an operational scheduling hint, not proof of current
ownership. A terminal seal accounts for every acquired claim and identifies its
release event or an exact reconciliation disposition. Mutable lease/status rows
may accelerate queries but cannot authorize a protected action. Acquisition
repeats the full submitted claim tuple: key, class, scope, mode, quantity, unit,
and limit kind. An unreleased claim called expired or requiring reconciliation
binds an exact `resource-claim-reconciliation` observation through the owner
port; lease time alone can never prove release.
Every acquisition is attempt-owned, and its `claim_key` must occur in that
exact attempt record's `resource_claim_keys`; the broader submission request
does not silently grant every attempt every claim.

Mutation state is an explicit classification:

```text
not-started
temporary-residue
immutable-output-published
reference-committed
protected-mutation-started
protected-mutation-partial
protected-mutation-completed
external-mutation-started
external-mutation-partial
external-mutation-completed
external-mutation-indeterminate
```

The validator rejects an unexplained regression. Cleanup may move
`temporary-residue` back to `not-started`; otherwise a history progresses from
no mutation toward the applicable immutable, reference, protected, or
external branch. A `mutation-state-changed` body must name the previous
event state and the event's new state.

The submitted mutation boundary fixes the only admissible branch:

| `mutation_boundary` | Admissible non-temporary states |
| --- | --- |
| `none` | `not-started` only |
| `immutable-publication` | `immutable-output-published` |
| `reference-update` | `immutable-output-published`, then `reference-committed` |
| `protected-state` | protected started/partial/completed |
| `external-side-effect` | external started/partial/completed/indeterminate |

Every boundary also permits `temporary-residue` before cleanup. Successful
external work is exactly `external-mutation-completed`; it is not mislabeled as
a protected mutation. Unknown external outcome is exactly
`external-mutation-indeterminate`.

Terminal compatibility is exact:

- `succeeded` has a result and permits `not-started`, published immutable output,
  a committed reference, completed protected mutation, or completed external
  mutation;
- `failed` has a failure and cannot claim the indeterminate external state;
- `cancelled-before-mutation` has an observed cancellation and only
  `not-started` or `temporary-residue`;
- `cancelled-after-mutation` has an observed cancellation and a proven
  immutable/reference/protected/external post-boundary state; and
- `indeterminate` has a failure/reconciliation descriptor and exactly
  `external-mutation-indeterminate`.

Result and failure descriptors are mutually exclusive for both the
`terminal-ready` event and terminal seal, and their values must agree.

## Crash reconciliation

A crash, lost worker, expired scheduling hint, or corrupt terminal write is not
silently converted to failure. The history records `orphaned`, then an exact
`recovery-classified` event before resume, retry, or terminal sealing. Recovery
binds the same attempt, session, and process owner tuple as the orphaned event.
A second orphan cannot replace an unresolved first orphan, and an
existence-only recovery later in the prefix cannot authorize an earlier retry
or terminal event. Recovery
classifications mean:

| Classification | Required mechanical consequence |
| --- | --- |
| `no-mutation-began` | state is `not-started`; retry is safe |
| `create-new-residue` | state is `temporary-residue`; exact residue is bound |
| `manifest-published-ref-unmoved` | state is `immutable-output-published`; manifest is bound |
| `ref-committed` | state is `reference-committed`; reference event is bound |
| `exact-process-still-live` | prior process is bound, revalidated, has no earlier exact `process-exited` event in the ledger, and may be resumed |
| `external-mutation-unproven` | state is `external-mutation-indeterminate`; manual reconciliation |
| `terminal-corrupt-or-incomplete` | terminal status is not trusted; exact reconciliation remains required |

Adoption never relies on PID alone: the complete process-identity tuple must be
revalidated. Terminal recovery obligations are retained rather than overwritten
by a mutable cleanup flag. Temporary residue, unmoved references, partial
protected or external mutation, indeterminate external mutation, and corrupt
terminal recovery produce exact ordered obligation classifications, actions,
and related events.

## Finite publication validation

The C02 validator is a separate offline registry. Validation order is canonical
domain, exact header/schema, semantic ordering and timestamps, content identity,
then cross-record relations. A complete terminal publication proves:

1. exactly one submission for the job and exact ContextRef/InputBinding closure;
2. a contiguous attempt chain and exact attempt/session/process ownership;
3. a contiguous event ledger with valid transitions and body/top-level pairs;
4. monotonic progress, request-versus-observation cancellation, claim accounting,
   and crash-reconciliation consequences;
5. a recomputed event ledger root and complete seal membership; and
6. at most one logical terminal seal, with exact result/failure and
   outcome/mutation compatibility.

A partial active publication may omit a terminal seal. Once a terminal seal is
present, its prefix is complete and immutable.

No record in this six-kind C02 operational family, especially no progress
event, can be the candidate of a C01 admission record. A handler that wants
Atlas evidence must publish a separate
C01 object/evidence record under an application-owned adapter and explicitly
bind the operational provenance. This preserves the boundary between runtime
telemetry and admitted knowledge.
