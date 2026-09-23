# Workbench storage inventory V1

Status: experimental product-generic read-only inventory contract

`workbench-storage-inventory-v1` is the bounded account of storage visible
below one Workbench `.workbench` root. Its `schema_version` is `1`, its
`canonicalization_id` is `workbench-canonical-json-v1`, and `read_only` is
always `true`. The machine-readable contract is
[storage-inventory-v1.schema.json](../../../core/src/workbench_core/storage/schemas/storage-inventory-v1.schema.json).

The inventory answers where an item is, who has custody of it, how much space
it occupies, when it was last used to the best available evidence, whether it
has a supported recreation route, and whether it may enter a cleanup plan. It
does not authorize deletion, establish pack or platform facts, or reinterpret
Crucible or Atlas evidence.

## Identity and scan boundary

`workbench-canonical-json-v1` is UTF-8 JSON with sorted object keys, no
insignificant whitespace, no trailing newline, and non-ASCII characters
retained as UTF-8. `inventory_id` is `workbench-storage-inventory:sha256:` plus
the SHA-256 of every top-level member except `inventory_id`.

`workspace_root` and `storage_root` record the resolved roots used for the
scan. Every item carries both its resolved `path` and its workspace-relative
`.workbench/...` path. A producer must reject a storage root or item path that
traverses a symlink, contains an unresolved parent, or escapes the selected
root. The scanner never follows symlinks and does not content-hash the whole
store. Its observation identities are metadata/tree observations, not content
digests and not evidence seals.

`scan.started_at` and `scan.completed_at` bound the scan. `root_device` may be
null when the host cannot expose a stable device number. Problems encountered
while walking are retained rather than causing an affected path to disappear
from the view.

## Items, components, and references

An item is one cleanup-addressable storage unit. `item_id` is stable for the
selected storage-root identity and relative path; it is not a content ID.
`observation_sha256` binds the lstat-derived tree observation used to detect
drift before a later operation. Any change that affects the observed tree must
produce a different observation digest even when the stable item ID remains
the same.

Kinds cover known operational families and `unknown`. Directory naming alone
may select a tentative kind, but it does not establish managed custody.
`parent_item_id` and `components` expose worlds, captures, logs, screenshots,
and other useful subdivisions without making each subdivision independently
deletable. A component path must lie under its parent item. Inventory totals
describe the whole scan and are not computed by naively summing overlapping
parent items and components.

References name containment, producer, snapshot, trash, and runtime
dependencies. A reference may resolve to an item ID or remain path-bound when
the target is outside the current item set. Required inbound references block
automatic cleanup. Missing or ambiguous references are problems, not absence.

## Custody and filesystem owner

Custody states are:

- `managed`: a valid Workbench manager manifest or equivalent current receipt
  owns the resource;
- `recognized-legacy`: a supported historical record identifies the producer
  but does not grant manager mutation authority;
- `external`: an explicitly external or imported resource; and
- `unknown`: no trustworthy ownership record was found.

`custodian`, `producer`, pack profile, and platform profile are nullable
because an honest unknown is better than an inferred owner. `evidence_paths`
names the local records supporting the classification. Filesystem uid, gid,
user, and group are a separate observation. `mixed: true` means the item has
more than one observed filesystem owner or the scanner could not reduce it to
one owner. Filesystem ownership never substitutes for logical custody.

## Space accounting

Every item and component reports:

- `logical_bytes`: the sum of regular-file lengths at each visible path;
- `unique_allocated_bytes`: allocated blocks counted once per `(device,
  inode)` within that item;
- `exclusive_allocated_bytes`: allocated blocks whose observed inode has no
  path outside that item in the same completed scan;
- regular-file, directory, and symlink counts.

This distinction is required because retained historical iterations may share
hard-linked files. Logical size is useful for understanding contents;
exclusive allocated size is the best bounded estimate of space a removal
could reclaim. It remains an estimate until a completed purge receipt records
the actual result. Sparse files and filesystem allocation rounding mean the
three byte values need not have a simple ordering.

V1 has no `special_count`. Sockets, devices, fifos, unreadable entries, and
other unsupported filesystem objects are recorded in `problems` and force a
conservative deletion state. Symlinks are counted using lstat and never
followed.

## Last use and reproducibility

Last-use state is `observed`, `inferred`, or `unknown`. An observed timestamp
comes from a manager ledger or exact producer receipt. A filesystem mtime is
always `inferred`; it must never be presented as observed use. Unknown use has
`at: null`, `basis: unknown`, and no invented timestamp. V1 deliberately has
no raw `last_modified_at` field: modification time is represented only as the
explicitly qualified fallback basis for last use.

Reproducibility states are:

- `verified`: the owning authority explicitly verifies the applicable
  reproducibility claim;
- `regenerable`: the item is a cache, build product, or temporary projection
  with a declared producer that can recreate it;
- `recipe-available`: a currently available command and bound inputs can
  recreate the requested resource, without claiming byte equivalence;
- `retained-only`: deleting the item loses the only known retained state; and
- `unknown`: available records do not support a stronger statement.

Each state carries a plain-language reason and evidence paths. A reproduction
command in a development report alone is not verified reproducibility.

## Deletion and recoverability

Deletion state is `eligible`, `review`, `protected`, or `active`:

- `eligible` is a known inactive resource with a manager-supported trash path;
- `review` is Workbench-associated state whose loss needs an explicit retained
  output acknowledgement or snapshot;
- `protected` includes evidence/proof custody, unknown or external resources,
  personal or untracked worlds, unresolved references, legacy trash, unsafe
  filesystem objects, broad category roots, and the storage root itself; and
- `active` means a current lease, running report, or validated live process
  prevents mutation.

Eligible items use recoverability `trash`. Protected and active items use
`none`. Review items may use `trash`, `snapshot-first`, or `none` depending on
their exact custody and reference state. `reason_codes` are mandatory and
must explain the result. Inventory classification is advisory input to a
fresh, separately validated storage-operation plan; it is never mutation
authorization.

## Determinism and validation

Items are ordered by ascending relative path and then item ID. Components,
references, evidence paths, reason codes, problems, and limitations have
deterministic producer-defined order and no semantic duplicates. The document
semantic validator checks canonical IDs, declared path containment, parent and
reference closure, aggregate and problem totals, declared byte-accounting
relationships, and protection from required inbound references. It does not
`lstat` current paths, recompute tree observations, discover live processes,
or independently establish custody, last use, or reproducibility.

The inventory producer performs the filesystem walk and derives those
observations and classifications. A mutation executor inventories again and
compares the newly observed item and policy with its plan rather than treating
a structurally valid inventory document as fresh authority.

The closed JSON Schema enforces the complete object shapes, enums, item ID and
digest forms, required reasons, timestamp nullability, and the basic
recoverability constraints. Changing a required member or its meaning requires
a new format version.
