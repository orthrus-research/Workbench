# Workbench storage operation receipt V1

Status: experimental incremental mutation-lifecycle contract

`workbench-storage-operation-receipt-v1` records what one validated
storage-operation plan actually did. Its `schema_version` is `1`, its
`canonicalization_id` is `workbench-canonical-json-v1`, and `operation` is the
same exact V1 operation named by its plan. The machine-readable contract is
[storage-operation-receipt-v1.schema.json](../../../core/src/workbench_core/storage/schemas/storage-operation-receipt-v1.schema.json).

The receipt is operational custody evidence, not Atlas interpretation,
Blueprint approval, compatibility proof, or permission to run another
mutation.

## Identity and plan binding

`receipt_id` is `workbench-storage-operation-receipt:sha256:` plus the SHA-256
of canonical JSON for every top-level member except `receipt_id`. `plan_id`
must identify the exact immutable plan revalidated immediately before the
operation began. Operation kind, ordered action IDs and kinds, workspace root,
and storage root must match that plan.

The receipt is written incrementally. Each atomic rewrite receives the
receipt ID for that complete current document; `plan_id` remains the stable
operation identity across running and terminal rewrites. A consumer must not
retain an early `receipt_id` and mistake it for the terminal record.

## Lifecycle

Top-level status is `running`, `complete`, or `failed`:

- `running` has no completion time or top-level error;
- `complete` has a completion time, no error, and every action complete; and
- `failed` has a completion time, an actionable top-level error, and at least
  one failed action.

Each action is written `pending` before it begins, then `running`, and finally
`complete` or `failed`. Timestamps, processed bytes, source/destination
relative paths, and the exact error are retained. Process termination or an
exception must leave the latest durable running or failed record; a partial
copy, corrupt snapshot, failed restore, or incomplete purge is never described
as complete.

## Results

`result.resource_ids` names newly published managed runtimes or world
snapshots. Cleanup records a `workbench-storage-trash:sha256:...` trash ID.
Restore names the new inventory item ID observed at the original path. Purge
records `purged_bytes`, the allocated bytes removed from the exact bound trash
payload, not a claim about concurrent whole-filesystem free-space movement.
`output_paths` lists retained manager records and resource payloads.

Running and failed receipts may have empty or partial result members. For a
complete receipt, the document semantic validator requires the following
operation-specific result declarations:

- runtime-create and world-restore declare one managed-runtime ID;
- world-snapshot declares one world-snapshot ID;
- cleanup declares a new trash ID and no reclaimed-byte claim;
- restore-trash declares the restored inventory item ID; and
- purge-trash is the only operation that may report removed allocated bytes.

When the plan is supplied, those IDs and retained output paths must match its
actions and effects. These are document claims. The executor, not the receipt
validator alone, establishes that a manifest was published, the restore
destination was absent, the exact payload moved, or completed cleanup custody
authorized a purge.

## Failure and recovery custody

New-destination operations stage into a fresh manager path. On failure they
remove or quarantine only incomplete new data, never their source. Cleanup
durably writes its transaction intent before attempting the same-filesystem
atomic rename and retains enough source and destination data for restore. A
matching valid `running` or `failed` cleanup receipt plus that intent is an
interrupted cleanup and grants restore-only custody; only a matching
`complete` cleanup receipt grants purge custody. A failed restore leaves the
original trash payload authoritative. Purge is irreversible; a failed purge
records what was processed and does not claim recoverability for already
removed bytes.

The receipt never expands the plan's authority. It cannot make an external
world managed, convert protected evidence to cache, waive review, accept a
different confirmation token, change a profile, or substitute an arbitrary
path. Trash without a matching valid cleanup receipt and manager transaction
intent is legacy trash and remains protected. An interrupted cleanup meeting
both requirements remains in the narrower restore-only state; it cannot be
purged.

## Validation

The document semantic validator recomputes `receipt_id`, checks canonical root
and output-path relationships, timestamps and legal action/status shapes,
terminal result requirements, and output/resource uniqueness. When supplied
the bound plan, it also checks plan, operation, ordered action, endpoint, and
planned-output equivalence. It validates the receipt document and its declared
plan relationship; it does not inspect a runtime or snapshot manifest on disk,
prove trash lineage from ledger files, observe restore destination absence, or
recheck a purge token against live custody.

Those composite checks belong to the operation producer/executor. Immediately
before and during mutation it must revalidate the plan and current inventory,
load and validate the relevant managed manifest or transaction record, enforce
destination absence and exact trash lineage, and compare the purge
confirmation with the manager-created trash identity. Only then may it write a
terminal receipt. Action arrays retain plan order; output paths and resource
IDs contain no semantic duplicates.

The closed JSON Schema enforces exact lifecycle and operation vocabularies,
terminal timestamp/error rules, complete-action closure, failed-action
presence, path and ID forms, nonnegative byte accounting, and the manager
trash ID namespace. Changing required lifecycle, result, or recovery meaning
requires a new format version.
