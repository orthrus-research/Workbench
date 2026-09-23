# Atlas experimental Anvil worldgen fingerprint V1

Status: implemented experimental read-only observation and comparison.

V1 accepts one explicitly selected, stopped legacy Anvil world only after the
Atlas structural observer reports no findings. It binds `level.dat` and every
region file by SHA-256, records the seed, generator, generator options, and
spawn, then emits coordinate-keyed fingerprints for saved generation output.

For each chunk, V1 separately fingerprints:

- block IDs and metadata for each legacy section;
- biome and heightmap arrays;
- the saved `Structures` compound;
- block and sky lighting; and
- `TerrainPopulated` and `LightPopulated` state.

The combined `worldgen_sha256` excludes entities, tile entities, scheduled
ticks, inhabited time, save timestamps, and other runtime-only fields. This
makes fixed-seed comparisons useful without pretending that byte-identical NBT
is required. Saved blocks include both base terrain and completed population
effects; V1 cannot reconstruct which generator placed a differing block.

V1 hashes raw legacy block IDs. Independently created worlds can assign
different numeric IDs to the same Forge resource location, so a differing
block hash is not automatically a semantic block difference. The separate
block-delta V2 authority resolves each world's saved registry before comparing
states. This V1 behavior remains unchanged to preserve retained identities.

The fingerprint format is
`atlas-experimental-anvil-worldgen-fingerprint-v1`. The comparison format is
`atlas-experimental-anvil-worldgen-comparison-v1`. Both identities are SHA-256
hashes over every other field, and comparison rejects a fingerprint whose
recorded identity does not match its contents.

Atlas detects mutation around each file read but cannot prove the client stayed
stopped across the multi-file operation. Workbench Shell owns that lifecycle
boundary and artifact retention. The result is non-normative, is not an Atlas
publication, and does not by itself attribute a difference to RTG or another
mod.
