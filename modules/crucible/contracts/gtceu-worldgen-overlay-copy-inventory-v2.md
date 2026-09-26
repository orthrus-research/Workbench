# GTCEu overlay copied-tree inventory V2

Status: source-only preparation for Core custody of a future GTCEu Forge
overlay. This contract does not install an overlay, change the V1 materializer,
or prove a live Forge process is absent.

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

This inventory is a prerequisite only. A later Core transaction must allocate
and publish the complete overlay envelope, persist the emitted chunks and
manifest, recheck the source while copying, verify the operation result and
sibling receipt, and persist restart recovery. Core ManagedTrees V1 admits
at most 4,096 members; the current Core TransportTrees V2 admits at most
10,001 files and 136 MiB, with bounded path depth/length and fixed file and
directory modes. The V2 domain inventory intentionally accepts some source
trees outside those Core bounds. The overlay route must resolve that capacity
and mode gap explicitly before claiming complete V1 input coverage or making
Core publication available. An explicit Core capacity refusal is not a
successful overlay materialization.
It must never take deletion authority over the external jar or source config.
