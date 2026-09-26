# GTCEu overlay copied-tree inventory V2

Status: opt-in Linux Core custody and publication of a fresh GTCEu Forge
overlay envelope. This contract does not install an overlay into a runtime,
change the V1 source-only materializer, or prove a live Forge process is absent.

## Selected entries and policy

`build_gtceu_overlay_copy_inventory` binds the exact V1 worldgen source
inventory ID and the bytes that V1's `materialize_overlay` would copy:
optional `dimensions.json` and `worldgen_extracted.json`, the created
`worldgen` parent, and every file and directory below `worldgen/fluid` and
`worldgen/vein`. It includes hidden and non-JSON sidecars. It does not inventory
unselected siblings elsewhere under `config/gregtech`, the external GTCEu jar,
or the later operation result. The caller must retain the V1 jar/source check
and recheck this copied-tree inventory at the copy boundary. This module has
no destination writer or cleanup authority.

The V2 route requires Linux no-follow directory handles and readable mount IDs.
It refuses symlinks,
hard-linked files, special entries and nonportable relative paths in its
selected copy set. It pins each source directory, reads files by directory
handle without following links, and checks identity, mode, size and timestamps
before and after reading. It also rejects a child mount, including a same-device
bind mount. A root path that changes during capture is refused.
The V1 source-only inventory and materializer keep their historical behavior
and bytes. Windows, macOS and Linux hosts without the required operations have
no V2 capture claim.

Each canonical JSONL entry has a portable slash-separated `path`, `kind`, and
POSIX numeric `mode`. A file also has `size_bytes` and SHA-256 of its complete
bytes. The synthetic `worldgen` parent has `mode: null` because V1's copy
creates that parent rather than copying the source directory mode. Directory
rows precede their children, and all rows are sorted by relative path.

## Bounded records and verification

The builder first scans the source and compares the V1 definition binding,
then emits canonical JSONL chunks through the caller's ordinal callback.
Every chunk is at most 1 MiB; the returned canonical manifest is at most
16 KiB regardless of entry count. It records the chunk count, ordered chunk
digest, entry-stream digest, file/directory counts and total file bytes, then
binds those fields in a content ID. There is no 4,096-entry or 4 MiB
aggregate manifest limit in this domain inventory. An individual path row
that cannot fit one 1 MiB chunk is explicitly refused. Chunks emitted before
a failed second source scan have no accepted manifest and must remain under
the caller's incomplete-attempt custody.

`parse_gtceu_overlay_copy_inventory` requires the exact supplied chunk count,
canonical row bytes, strict ordering, parent closure, required worldgen
subtrees, row fields and aggregate digests. The storage owner must also refuse
extra physical chunks it did not pass to the parser.
`verify_gtceu_overlay_copy_source` requires the caller's previously selected
V2 inventory ID, compares every reopened row to a fresh
no-follow source scan and rechecks the V1 definition binding. New or changed
sidecars therefore invalidate the capture even when V1 JSON definitions are
unchanged.

## Opt-in Core envelope route

The V2 command retains the validated plan and copied-tree inventory under a
Core attempt, checks the source jar and configuration against the selected V1
inventory, and seals ordered Crucible-generated effect bytes. Core checks the
exact POSIX V3 member and byte upper bounds before its first copy. It then
copies under Linux no-follow handles, applies those effects, writes the V1
output inventory and materialization JSON siblings, verifies the entire
staged envelope, and publishes the one fresh `config` tree through Core's
no-replace transaction. The output is `config/gregtech` plus its two sibling
JSON files. Crucible supplies read-only validators; Core owns the durable
records, stage, writes, and publication.

```bash
python3 modules/crucible/tools/materialize_gtceu_worldgen_overlay_v2.py \
  materialize --jar <gregtech.jar> \
  --config-root <source>/config/gregtech \
  --inventory .workbench/evidence/gtceu/<label>/inventory.json \
  --plan <overlay.json> \
  --out-config-root .workbench/overlays/<label>/config/gregtech

python3 modules/crucible/tools/materialize_gtceu_worldgen_overlay_v2.py review
python3 modules/crucible/tools/materialize_gtceu_worldgen_overlay_v2.py \
  reconcile --attempt-id <id-from-review>
```

The command's default workspace is this checkout and its Core records use the
stable per-user Workbench configuration home. `--workspace` and
`--configuration-home` select another exact context and precede the command
name. A relative output path starts at the selected workspace. The output
path is an explicit caller choice; use a fresh ignored Workbench location.
A second run at the same target fails without replacing
it. `review` inventories retained attempts without changing them. `reconcile`
only completes an exact Core publication intent after a process interruption;
earlier incomplete stages remain review-only.

Core's opt-in V3 profile admits at most 100,000 files, 100,000 directories,
2 GiB per file, and 32 GiB for the whole tree, including conservative space
reserved for both sibling JSON files before copying. The V2 domain inventory
accepts some sources outside these bounds. A pre-copy `overlay.unsupported`
exit is a bounded refusal, not a successful materialization; the retained
attempt remains visible in `review` without a copied payload or target.
Later failures can retain an incomplete private stage and must not be reported
as published. The route never takes deletion authority over the external jar
or source configuration. Windows and macOS cannot use the V2 Linux capture and
copy path. WSL can report itself as Linux, but its filesystem behavior has not
been qualified for this route. Linux source-level tests do not establish Forge
runtime or save compatibility.
