# Workbench managed runtime V1

Status: experimental product-generic managed-resource contract

`workbench-managed-runtime-v1` records one Workbench-created disposable
Cleanroom runtime and its reserved or generated world. Its `schema_version` is
`1` and `canonicalization_id` is `workbench-canonical-json-v1`. The
machine-readable contract is
[managed-runtime-v1.schema.json](../../../core/src/workbench_core/storage/schemas/managed-runtime-v1.schema.json).

The record is operational custody metadata. It does not establish Atlas
knowledge, Blueprint approval, mod compatibility, whole-world determinism, or
proof-mode repeatability.

## Stable resource identity

`runtime_id` is `workbench-managed-runtime:sha256:` plus the SHA-256 of
canonical JSON for every top-level member except `runtime_id`. It identifies
one exact version of the mutable operational record. When lifecycle state,
last use, content observation, references, or limitations change, the record
receives a new runtime ID. The inventory `item_id`, derived from the selected
storage root and canonical resource path, remains the stable selector across
those record versions.

`relative_path` is the canonical original resource path and remains stable
while a cleanup receipt temporarily holds the payload in manager trash.

## Profile and source authority

V1 is the exact dedicated-server Minecraft 1.12.2 slice. Every runtime binds
an explicit pack profile, its exact file and canonical parsed digests, the
platform profile, and the Cleanroom version. Supersymmetry is not an implicit
default. The pack/platform profile owns runtime requirements, world type,
server artifact identity, configuration semantics, and restore compatibility;
the generic manager owns filesystem safety and custody.

`source_template` binds the resolved template, its inventory digest, and the
exact server JAR. `audit_safe: true` means the same structural audit used by
the disposable runner accepted the template immediately before copying. It is
not a claim about runtime behavior. The manager must recheck this binding when
executing a creation plan rather than treating a prior preview as approval.

## Disposable copy and fresh-world boundary

The manager creates the runtime only at a fresh, non-symlinked path beneath
the selected `.workbench` root. It excludes every recognized world/save,
logs, caches, crash residue, captures, screenshots, and other profile-declared
run residue. Every retained regular file is independently copied. V1 neither
hard-links nor reflinks mutable runtime content to the source template.

`world.state: reserved-absent` means the configured `level-name` path did not
exist when creation completed. Minecraft, not the manager, creates a real
world during launch. A producer must not call an empty directory a generated
fresh world. Later observations may change state to `present` or
`snapshotted`, but may not erase the original seed, world type, or level-name
binding.

The creation path never mutates or deletes a template world. Existing worlds
are protected assets even when found under a historical `.workbench/runs`
directory.

## Lifecycle and content observation

Runtime state is `ready`, `active`, `stopped`, or `trashed`. `active` requires
a current manager lease or validated live process. `last_used_at` is null
until the manager or owning runner records a use; filesystem mtime alone must
not silently populate it.

`content.tree_observation_sha256` is an lstat-derived tree observation at the
time this record was emitted. It is not a content hash or proof seal. Content
records logical and unique allocated bytes, entry counts, zero followed or
retained symlinks, and the mandatory independent-copy invariant. A later
operation must inventory the runtime again and reject a stale observation.

References name the creation plan, template, pack/platform inputs, or other
resources on which the runtime depends. Nullable resource IDs and paths allow
the record to be honest about dependencies that have not been admitted as
manager resources. Required unresolved references protect the runtime from
automatic cleanup.

## Validation and limitations

Reference arrays and limitations use deterministic producer order and contain
no semantic duplicates. The document semantic validator recomputes
`runtime_id`, checks canonical root and relative-path relationships,
source/destination separation, the bound direct level-name path, and reference
well-formedness. It validates what the record says; it does not inspect the
live filesystem, rerun the template audit, compare current inodes, establish
destination absence, or infer a lifecycle transition from a running process.

Those live properties belong to the producer or operation executor. Before
publication or a later mutation, that code must audit and inventory the actual
paths again, verify profile and server-JAR content bindings, confirm the
independent copy and fresh-world boundary, and reject stale observations.

The closed JSON Schema enforces the exact dedicated-server shape, digest and
identity forms, safe-audit and independent-copy constants, zero symlinks,
signed 64-bit seed, and fresh-world state vocabulary. The record does not
authorize launch, cleanup, restore, or purge; each mutation requires a fresh
storage-operation plan and receipt. Changing required V1 meaning requires a
new format version.
