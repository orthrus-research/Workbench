# Workbench world snapshot V1

Status: experimental immutable managed-world snapshot contract

`workbench-world-snapshot-v1` records one independently copied, per-file
hashed Minecraft world below Workbench custody. Its `schema_version` is `1`,
its `canonicalization_id` is `workbench-canonical-json-v1`, and a published
snapshot has `state: complete`. The machine-readable contract is
[world-snapshot-v1.schema.json](../../../core/src/workbench_core/storage/schemas/world-snapshot-v1.schema.json).

A complete snapshot preserves bytes before a risky disposable test. It does
not authorize mutation of its source, claim a transactional Minecraft save,
or make an unknown personal world disposable.

## Identity and source binding

`snapshot_id` is `workbench-world-snapshot:sha256:` plus the SHA-256 of
canonical JSON for every top-level member except `snapshot_id`. The complete
record is immutable. Staging and failed copies are described by a storage
operation receipt and must never be published by changing the meaning of a
complete snapshot.

The source binds an inventory-issued world item, its managed runtime, resolved
and workspace-relative paths, the exact pre-copy observation digest, level
name, signed 64-bit seed, and world type. V1 snapshots only manager-owned
dedicated-server worlds. An external or recognized-legacy world may be
inventoried and protected, but it cannot be silently upgraded to managed
custody by copying it.

The profile repeats the exact pack, platform, Cleanroom, and Minecraft 1.12.2
bindings required to interpret and later restore the save. Profile identity
comes from the pack/platform profiles; the generic manager does not infer
compatibility from a directory name or `level.dat` alone.

## Quiescence and consistency

V1 requires `manager-quiesced`: no manager lease is active, the source
observation before and after the copy is equal, and `source_stable` is true.
The producer must fail if files appear, disappear, change size or metadata, or
change digest while copying.

Manager quiescence is a bounded filesystem statement. It does not rule out an
unmanaged external process that bypasses Workbench, prove a clean Minecraft
shutdown, or provide filesystem-transaction semantics. `statement` and
`limitations` must retain that boundary rather than presenting the copy as a
game-level checkpoint.

## Exact content

`files` lists every regular file below the world root in ascending relative
path order. Each entry binds relative path, logical size, allocated size, and
SHA-256. Paths are relative, contain no `..`, and never escape the payload.
Directories are implied by those paths. A complete Minecraft world must
contain `level.dat` or `level.dat_old`.

`content.tree_sha256` is the SHA-256 of canonical JSON for the ordered `files`
array. `file_count`, `logical_bytes`, and `unique_allocated_bytes` must match
that array and copied tree. The snapshot follows and retains no symlink,
socket, device, fifo, or other special entry. Every file has an inode
independent from its source. A producer writes into a fresh staging directory,
validates the complete copy, then atomically publishes it at the declared
`.workbench` payload path.

## Restore boundary

Restore never overwrites a world or runtime. V1 restores by creating a new
profile-compatible managed runtime and copying the snapshot into its absent
world path. Exact pack profile, platform profile, Cleanroom version, side,
world type, and relevant runtime bindings must match. Migration to another
profile or save format is a separate profile-authorized capability, not an
implicit restore fallback.

The snapshot is retained-only data unless another owning authority explicitly
establishes a verified recreation route. Inventory therefore does not mark it
automatically eligible for deletion.

## Validation

The document semantic validator recomputes `snapshot_id` and `tree_sha256`,
requires unique sorted file paths, verifies the declared aggregate sizes and
file count, confirms a declared root level record, checks agreement among the
recorded source observations, and checks the declared payload/root
relationship. It validates internal record consistency; it does not open the
payload, hash its current files, compare source and destination inodes, detect
a live Minecraft process, or establish current profile compatibility.

The snapshot producer and restore executor perform those live checks at their
respective boundaries: they inventory and hash actual files, reject unsupported
entries or active/stale sources, verify independent copies, and revalidate the
profile/runtime binding before publication or restore. The closed JSON Schema
enforces the complete record shape, exact dedicated-server profile, immutable
completion state, safe relative paths, per-file digests, zero symlinks, and
independent-copy constants.

Changing a required member, the copy consistency meaning, or restore
compatibility rules requires a new format version.
