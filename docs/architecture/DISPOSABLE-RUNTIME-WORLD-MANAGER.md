# Disposable runtime, world, and storage manager

Status: implemented experimental local dedicated-server workflow.

## Purpose

The manager inventories Workbench local storage, creates independent managed
runtimes, snapshots stopped managed worlds, restores snapshots only into fresh
runtimes, and quarantines eligible resources before permanent deletion.

The public Core command uses platform user-state for source and installed
execution unless setup or `WORKBENCH_STATE_ROOT` selects another location.
Historical checkout `.workbench/` stores remain in place until separately
inventoried. Finding a path inside a selected store does not make it disposable.
Every mutation
of an existing item starts from one exact inventory item ID, binds the observed
state, and repeats inventory and path-safety checks immediately before
execution. Unknown custody, external or personal worlds, evidence, active
resources, unresolved references, symlinks, special files, broad roots, and
paths outside the selected Workbench store fail closed.

## Commands

Inventory is read-only:

```bash
python3 tools/workbench.py storage list
python3 tools/workbench.py storage list --json
python3 tools/workbench.py storage inspect <storage-item-id> --json
```

Create an independent runtime from an audited profile template:

```bash
python3 tools/workbench.py runtime create \
  --profile supersymmetry \
  --label risky-change \
  --show

python3 tools/workbench.py runtime create \
  --profile supersymmetry \
  --label risky-change
```

`runtime create` executes unless `--show` or `--json` is present. The profile
is always explicit; `--runtime-template`, seed, level name, and loopback server
port may be overridden.

After Minecraft creates a world in that runtime, snapshot it by the runtime's
inventory item ID:

```bash
python3 tools/workbench.py world snapshot <runtime-item-id> \
  --label before-risk \
  --show

python3 tools/workbench.py world snapshot <runtime-item-id> \
  --label before-risk
```

`world snapshot` also executes unless `--show` or `--json` is present. Use
`--world <level-name>` when the runtime contains more than one world.

Restore, cleanup, and trash restore are preview-first and require `--apply`:

```bash
python3 tools/workbench.py world restore <snapshot-item-id> \
  --profile supersymmetry \
  --label restored-test
python3 tools/workbench.py world restore <snapshot-item-id> \
  --profile supersymmetry \
  --label restored-test \
  --apply

python3 tools/workbench.py storage cleanup <storage-item-id>
python3 tools/workbench.py storage cleanup <storage-item-id> --apply

python3 tools/workbench.py storage restore <trash-item-id>
python3 tools/workbench.py storage restore <trash-item-id> --apply
```

A `review` item additionally needs `--allow-review`; that option cannot
override `protected` or `active`. Cleanup only moves the item to recoverable
trash and reclaims no space.

Permanent purge is separate and irreversible. Its selector is the trash
inventory item ID; its confirmation is the different trash resource ID shown
by `storage inspect`:

```bash
python3 tools/workbench.py storage purge <trash-item-id> \
  --confirm <trash-resource-id> \
  --show

python3 tools/workbench.py storage purge <trash-item-id> \
  --confirm <trash-resource-id>
```

Purge executes only when the confirmation matches the completed manager trash
transaction. `--show` or `--json` always prevents execution.

## Inventory and identity

The read-only
[storage inventory V1](../../modules/crucible/contracts/storage-inventory-v1.md)
walks the selected store's `.workbench` root with `lstat`, never follows
symlinks, and emits unknown roots instead of hiding them. Components such as
worlds, logs,
captures, and reports remain part of their containing cleanup item rather than
becoming silent deletion targets.

Its three identities have separate roles:

- `item_id` selects one stable storage-root-relative item; paths, globs,
  traversal, the workspace, `.workbench`, and category roots are rejected;
- `observation_sha256` binds the metadata-derived tree and makes a stale plan
  fail; and
- `resource_id` identifies a versioned managed runtime, snapshot, or trash
  transaction and is not interchangeable with an item ID.

Metadata references protect required inputs. Missing, ambiguous, or unsafe
metadata makes the classification more conservative. Logical custody
(`managed`, `recognized-legacy`, `external`, or `unknown`) is distinct from
filesystem ownership; being owned by the current operating-system user grants
no deletion authority.

Inventory reports logical, unique allocated, and bounded exclusive allocated
bytes so hard-linked storage is not counted as safely reclaimable twice. Last
use and reproducibility carry explicit evidence levels. Deletion is separately
classified as `eligible`, `review`, `protected`, or `active`, with a stated
recovery mode. These fields inform a fresh operation plan; inventory never
authorizes mutation by itself.

## Managed runtime boundary

[Managed runtime V1](../../modules/crucible/contracts/managed-runtime-v1.md)
requires an explicit pack profile and an already available compatible runtime
template. It binds the source profile and server artifact, excludes old worlds,
logs, caches, crashes, and captures, copies retained files to independent
inodes, stages under a fresh destination, and publishes atomically. It never
mutates the source template or overwrites an existing destination.

The manager writes loopback-only offline dedicated-server configuration. Its
initial world state is `reserved-absent`: no world directory exists when
creation finishes. Minecraft, not the manager, creates the world during a later
launch. The manager does not acquire pack artifacts, JDKs, or templates and
does not launch the server.

Supersymmetry is one explicit profile, never an implicit generic default.

## Snapshot and restore boundary

[World snapshot V1](../../modules/crucible/contracts/world-snapshot-v1.md)
accepts only a world inside one exact manager-owned runtime. The runtime must
not be active, the world must contain `level.dat` or `level.dat_old`, and every
entry must be a regular file or directory. The manager hashes and independently
copies each file, verifies source stability and the copied payload, then
publishes an immutable snapshot.

`manager-quiesced` means Workbench saw no manager lease and observed a stable
filesystem tree across the copy. It does not prove a clean Minecraft shutdown,
block unmanaged processes, or create a transactional game save.

Restore requires matching pack, platform, Cleanroom, side, world type, seed,
and relevant runtime context. It audits a compatible template and copies into
a fresh runtime's absent world path. It never restores in place, overwrites, or
merges with an existing world.

## Cleanup and recovery boundary

The [storage operation plan V1](../../modules/crucible/contracts/storage-operation-plan-v1.md)
is inert and binds inputs, actions, effects, blockers, reclaimable-space
estimates, and recovery. Execution validates the complete plan against a fresh
observation; a preview is not an approval token.

Cleanup uses a same-filesystem atomic rename into one manager-created trash
child inside the selected store. A durable transaction intent binds the
original item, path, observation, and quarantine path before the move. Restore
succeeds only while the exact original path is absent.

An interrupted cleanup with a valid intent and matching `running` or `failed`
receipt is restore-only. Purge requires a matching `complete` cleanup receipt;
unrecorded or legacy trash cannot use this path. Purge removes only the exact
validated payload, refuses symlinks and special files, and requires the exact
trash resource ID. Completed and interrupted operations retain a
[storage operation receipt V1](../../modules/crucible/contracts/storage-operation-receipt-v1.md)
inside the selected manager state store.

## Authority and limits

- Pack and platform profiles own runtime requirements, world type, server
  identity, defaults, and restore compatibility.
- Crucible owns generic inventory, filesystem custody, independent copies,
  hashing, trash transactions, plans, and receipts.
- The Workbench Shell exposes commands without creating another approval path.
- Atlas evidence cannot be relabeled as disposable cache by this manager.
- Blueprints owns construction and validation gates; Manuals may teach the
  workflow but cannot authorize mutations.

The manager supports bounded Minecraft 1.12.2 dedicated-server profiles and
exact compatible restores. It does not manage client or integrated-server
runtimes, personal-world imports, migrations, in-place restore, batch cleanup,
automatic retention, remote synchronization, or hostile same-user namespace
races. Its records establish local filesystem custody and operation history,
not mod compatibility, gameplay behavior, causality, or whole-world
determinism.


Retained check snapshots use this same inventory and transaction engine. See the
[manual check storage lifecycle](CHECK-SNAPSHOT-CONTRACT.md#manual-check-storage-lifecycle)
for `storage --checks`, exact pins, dependency-preserving trash, verified local
export, group allocation previews and reconciliation. These additions do not enable
automatic retention or grant ownership of external game instances.
