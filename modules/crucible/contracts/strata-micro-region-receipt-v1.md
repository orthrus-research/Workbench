# Crucible Strata micro-region receipt V1

Status: experimental generic physical-observation contract; not Atlas
admission, causal attribution, determinism proof, or visual approval

## Purpose

This contract extends the bounded Strata observation path from a hydrated tile
to a reviewable 16×16-chunk micro-region. The sample is 256×256 blocks and 256
chunks: large enough to show biome adjacency, coast and river continuity, and
mesoscale landform shape while remaining inexpensive enough for frequent local
iteration.

Strata region-manifest V2 carries two display surfaces derived from the same
lossless package:

- the exact `Chunk.getHeightValue` surface and exact biome columns; and
- a landform surface derived from exact dense sections by selecting the first
  non-air, non-foliage/non-artifact block below each column.

The landform surface removes canopies and common generated structure pillars
that obscure terrain at regional scale. It is explicitly display-only. Exact
dense sections, exact height columns, biome columns, resource voxels, and
terrain faces remain in the bound package and shards.

## Completion

A receipt requires an exactly populated 16×16 window, zero dense and sparse
World API mismatches, validated raw extraction and monolithic package, V2
preview validation against the exact shard payloads, renderer build, browser
smoke, and both overview and exact-tile screenshots. Unlike the V1 observation
receipt, it binds every JSON shard rather than only the manifest.

The receipt also separates four Java identities:

- Cleanroom runtime JDK;
- Gradle control-process JDK;
- observer source compiler JDK; and
- shipped observer classfile target.

The current proven split is Cleanroom Java 25, observer compiler Java 25,
Gradle host Java 21, and Java 8/classfile-major-52 observer output. Java 8 is a
consumer compatibility target and the RetroFuturaGradle patched-Minecraft
toolchain, not the default development runtime.

## Entry point

```bash
python3 modules/crucible/tools/run_strata_observation.py \
  --runtime .workbench/<runtime> \
  --java-cmd /absolute/path/to/java-25/bin/java \
  --sample-profile micro-region \
  --min-chunk-x <x> \
  --min-chunk-z <z> \
  --label <fresh-label>
```

The profile selects a 16×16 capture, 4×4 shards, manifest V2, modern observer
compiler discovery, and two screenshots. `--scan` may reuse a previously
validated dense scan in Workbench storage without starting Minecraft again.

Recurrent Complex remains a normal Forge decoration participant. This contract
adds no RC adapter, callback, registry, or package dependency.

The machine-readable form is
[`strata-micro-region-receipt-v1.schema.json`](../schemas/strata-micro-region-receipt-v1.schema.json).
