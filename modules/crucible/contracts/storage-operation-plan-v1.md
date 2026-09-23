# Workbench storage operation plan V1

Status: experimental product-generic inert mutation-plan contract

`workbench-storage-operation-plan-v1` is the complete preview produced before
one disposable-runtime, world-snapshot, or storage mutation. Its
`schema_version` is `1`, `canonicalization_id` is
`workbench-canonical-json-v1`, and `read_only_preview` is always `true`. The
machine-readable contract is
[storage-operation-plan-v1.schema.json](../../../core/src/workbench_core/storage/schemas/storage-operation-plan-v1.schema.json).

The plan explains and binds a possible operation. Producing or validating it
must not create a runtime, write configuration, copy a world, move an item,
restore trash, or purge bytes.

## Identity, readiness, and freshness

`plan_id` is `workbench-storage-operation-plan:sha256:` plus the SHA-256 of
canonical JSON for every top-level member except `plan_id`. Action and input
IDs are the equivalent canonical identities for their complete records. The
plan is immutable after publication.

Status is `ready` only when `blockers` is empty. A `blocked` plan has at least
one blocker explaining unsafe custody, unresolved paths, active use, stale or
missing input, unsupported profile, existing destination, review
acknowledgement, or confirmation failure. Attention is represented as an
explicit blocker or limitation; it is never collapsed into ready when it
changes whether mutation is safe.

Before execution, the Shell must resolve the same request again and require
the complete `plan_id` to remain unchanged. The executor then rechecks every
input item ID, observation digest, resource binding, profile digest, path,
non-symlink ancestor, lease, reference, and destination absence. A preview is
not an approval token and stale plans fail closed.

## Standalone parameters

The closed `parameters` object makes a serialized validated plan executable
without parsing the display `command` or relying on hidden process state. It
contains exactly:

- nullable `label`, signed 64-bit `seed`, `level_name`, and `server_port`;
- `allow_review`, which is true only when the caller explicitly accepts a
  review-class retained output for recoverable cleanup; and
- nullable `confirmation` for irreversible purge.

`runtime-create` resolves all four creation parameters and never allows review
or confirmation. `world-snapshot` resolves only a new snapshot label.
`world-restore` resolves a new runtime label, level name, and local server port;
its seed and saved-world context come from the bound snapshot rather than an
override. Cleanup has only `allow_review`. Restore-trash has no operation
parameter. Purge-trash has only a confirmation value, which must equal the
exact manager-created trash ID and recovery confirmation token.

`command` is copyable presentation of the same fields. Execution consumes the
structured parameters and actions, never shell-splits the rendered command.

## Operations

V1 supports exactly:

- `runtime-create`: audit one explicit profile/template, independently copy
  it into a new managed runtime, reserve an absent fresh world, and publish a
  managed-runtime record;
- `world-snapshot`: require a manager-quiesced world, independently hash/copy
  it into new staging, and atomically publish a complete snapshot;
- `world-restore`: create a new exact-profile runtime and copy a compatible
  snapshot into its absent world path;
- `cleanup`: atomically move explicit eligible item IDs, or explicitly
  acknowledged review item IDs, into new manager-owned trash;
- `restore-trash`: move one manager trash payload back only when its bound
  original path remains absent, accepting either a completed cleanup receipt
  or a matching prewritten transaction intent plus a valid `running` or
  `failed` cleanup receipt; and
- `purge-trash`: irreversibly remove only one completed manager-created trash
  payload after exact confirmation.

An interrupted-cleanup payload is restore-only and cannot enter a purge plan.
No operation accepts `.workbench` itself, a broad category root, the workspace
root, a glob, `..`, a symlinked ancestor or descendant, an unresolved path, or
an external personal-world path as a mutation target. Legacy trash without a
valid cleanup receipt and matching manager transaction intent cannot be
restored or purged by V1.

## Inputs, actions, and effects

Inputs bind the role, inventory item or resource identity, resolved and
workspace-relative path, current observation digest, and optional exact
content/profile binding. Every input supplies at least one concrete item,
resource, or resolved path identity. Profile-bound runtime/world operations
also carry a complete exact pack and platform profile object; Supersymmetry is
never implicit.

Ordered actions use the bounded kinds `audit-source`, `copy-independent`,
`reserve-fresh-world`, `write-manifest`, `move-to-trash`,
`restore-from-trash`, and `purge-trash`. Endpoints are structured records, not
paths extracted from prose. Actions name purpose, expected logical bytes, and
whether the action itself is recoverable. An operation-specific semantic
validator requires the appropriate actions and rejects contradictory or
duplicate actions.

`effects` gives one relative-path projection of new paths, moves, removals,
retained paths, and expected reclaimable bytes. Moving to trash has zero
expected reclaimed bytes because it retains allocation on the same storage
root. Only purge may predict nonzero reclamation, and actual reclaimed bytes
remain a receipt result.

## Recovery

Runtime create, snapshot, and world restore use `new-destination-only`: a
failure removes or quarantines only incomplete new state and never rolls back
over a source. Cleanup uses `restore-trash`. Restore-trash itself may use
`not-needed`, because its safe failure leaves the admitted trash payload in
place. Purge is `irreversible` and has an exact confirmation token.

An operation receipt, not the plan, owns running, failed, and completed
lifecycle state. Cleanup writes its bound transaction intent before the move.
If that intent is paired with a matching valid `running` or `failed` cleanup
receipt, V1 admits recovery to the original absent path but withholds purge
authority. Cleanup is recoverable only until a later completed purge.

## Concurrency boundary

Execution rejects observed symlinks and filesystem-device crossings and uses
file-descriptor-relative traversal for permanent purge. Those controls and the
fresh-input recheck detect ordinary drift; they do not make every
check-then-mutate path resistant to a hostile same-user process replacing
namespace entries concurrently. Mutations therefore require a quiesced
workspace with no competing mutator for the selected paths. This is an
operational precondition, not a property that document validation can prove.

## Authority and validation

The pack/platform profile owns runtime contents, side, world type, save
compatibility, and protected pack evidence. Crucible owns filesystem custody,
copying, snapshot hashing, trash, and receipts. Atlas remains the authority for
the meaning of retained evidence; the manager cannot relabel evidence as a
regenerable cache to make cleanup easier.

A document semantic validator recomputes identities, checks deterministic
ordering, derives readiness, validates operation-specific
parameters/actions/effects and recovery, verifies declared root containment,
and checks that deletion inputs bind one declared inventory. It does not
establish that inventory's current freshness or inspect live paths; the
executor performs those checks before mutation. The closed JSON Schema
enforces exact operation and action vocabularies, plan status/blocker shape,
standalone parameter nullability, profile requirements, bounded paths, and
irreversible purge confirmation.

Changing an operation, parameter, or authorization meaning requires a new
format version.
