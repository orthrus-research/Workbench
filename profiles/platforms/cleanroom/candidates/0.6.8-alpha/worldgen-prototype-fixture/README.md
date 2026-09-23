# Cleanroom 0.6.8-alpha World Studio prototype fixture

This is an active experimental implementation of a total Overworld replacement
for the exact Minecraft `1.12.2` / Cleanroom `0.6.8-alpha` candidate. Version
`0.4.0` builds a remapped mod, registers the explicit `wb_proto` world type,
and supplies its own `BiomeProvider` and complete `IChunkGenerator`.

It is a working vertical slice, not a production generator, an admitted
Blueprint, or evidence that every pack mod is compatible.

## What it does now

- publishes a validated, immutable, hash-identified generation plan from
  defaults or the optional GroovyScript `mods.worldStudio` property container;
- divides the world into deterministic jittered-Voronoi mega-regions;
- gives every mega-region a lithology, base terrain character, climate biases,
  weight, and constrained biome palette;
- evaluates independent octave fields for continentalness, temperature,
  moisture, relief, and surface variation;
- computes haloed coarse watershed tiles with Priority-Flood sink handling,
  deterministic D8 receivers, runoff accumulation, and Strahler order;
- lets lithology permeability reduce runoff and lithology erodibility scale
  physical channel incision;
- prefers installed Biomes O' Plenty registry identities in each regional
  palette and falls back to declared vanilla biomes when BOP is absent;
- fills the primer with the region's current lithology marker, carves physical
  river and stream channels, supplies water, and then invokes native biome
  surface replacement;
- selects cave and ravine generators through Forge
  `TerrainGen.getModdedMapGen`, so a normal Cave Generator installation can
  replace those implementations;
- calls native biome decoration once, including BOP's ordinary per-biome
  decoration path, while preserving Forge population gates; and
- computes a compact primitive-array sample once per chunk, shares it between
  the provider and generator, and bounds exact chunk, biome-point, and
  region-neighborhood caches;
- writes sampled JSON-line diagnostics prefixed `WORLDGEN_PROTOTYPE`, including
  the selected plan, mega-region, lithology, climate, hydrology distances,
  octave contributions, selected carvers, block counts, elapsed time, and
  sampling reuse; and
- emits custom JFR events for watershed tile builds, chunk sampling,
  generator/population stages, and Forge-facing biome-area request shapes.

The causal order is:

```text
seed + immutable plan
  -> mega-region and lithology
  -> continuous climate and relief
  -> rainfall + permeability + haloed watershed routing
  -> discharge-classified river/stream segments
  -> region-constrained biome
  -> base material and physical channels
  -> native biome surface
  -> Forge-selected caves and ravines
  -> custom population
  -> native/BOP biome decoration
  -> spawning, ice, and snow
```

The primary edit points are:

- `world/plan/WorldStudioPlan.java` for the stable plan contract and defaults;
- `world/WorldStudioTerrain.java` for region, field, climate, biome, lithology,
  and channel interpretation;
- `world/hydrology/WatershedEngine.java` for routing, accumulation, channel
  rasterization, and the bounded tile cache;
- `world/PrototypeChunkGenerator.java` for material and lifecycle order;
- `compat/groovy/WorldStudioPropertyContainer.java` for the script surface;
- `examples/groovy/postInit/world_studio_mega_regions.groovy` for a runnable
  pack-specific plan; and
- `diagnostics/PrototypeDiagnostics.java` for the sampled observation record.

The development-only summarizer converts one server log into stable JSON
counts without making a compatibility or causal claim:

```bash
python3 \
  profiles/platforms/cleanroom/candidates/0.6.8-alpha/worldgen-prototype-fixture/tools/summarize_world_studio_log.py \
  .workbench/runs/<run>/logs/latest.log
```

The companion JFR summarizer reads only the four World Studio custom event
types:

```bash
python3 \
  profiles/platforms/cleanroom/candidates/0.6.8-alpha/worldgen-prototype-fixture/tools/summarize_world_studio_jfr.py \
  --jfr-bin /path/to/run-jdk/bin/jfr \
  .workbench/runs/<run>/world-studio.jfr
```

For a same-seed optimization, compare every generation and population record:

```bash
python3 \
  profiles/platforms/cleanroom/candidates/0.6.8-alpha/worldgen-prototype-fixture/tools/compare_world_studio_logs.py \
  .workbench/runs/<run>/baseline.log \
  .workbench/runs/<run>/candidate.log
```

The comparator requires matching plan, seed, provider/generator, and carver
identities, complete clean runs, and identical chunk records. It excludes only
its versioned allowlist of timing and cache telemetry.

The generator does not call `GameRegistry.generateWorld`. Forge's patched
`Chunk.populate` owns that dispatch after `IChunkGenerator.populate` returns,
so independently registered `IWorldGenerator` implementations remain
independent. There is no Recurrent Complex import, adapter, callback,
structure API, or reflective dependency.

## Build

From the repository root:

```bash
gradle \
  --no-daemon \
  -p profiles/platforms/cleanroom/candidates/0.6.8-alpha/worldgen-prototype-fixture \
  --project-cache-dir .workbench/gradle-project-cache/worldgen-prototype-fixture \
  clean remapJar
```

The production-remapped jar is written below
`.workbench/build/cleanroom/0.6.8-alpha/worldgen-prototype-fixture/libs/`.
`runServer` additionally requires an absolute target inside this repository's
ignored `.workbench/` directory.

The fast loop is in
[Iterate the first playable worldgen replacement](../../../../../../modules/manuals/guides/experimental/worldgen-cleanmix/first-playable-worldgen.md).
The region, climate, channel, and Groovy controls are in
[Iterate mega-regions, hydrology, and BOP palettes](../../../../../../modules/manuals/guides/experimental/worldgen-cleanmix/iterate-mega-regions-hydrology.md).

Diagnostics default to enabled with one out of every 16 chunks sampled. The
run task accepts:

```text
-PworkbenchWorldgenDiagnostics=true|false
-PworkbenchWorldgenSampleModulo=<positive integer>
-PworkbenchWorldgenChunkSampleCacheChunks=<positive integer>
-PworkbenchWorldgenBiomePointCacheColumns=<positive integer>
-PworkbenchWorldgenRegionCacheCells=<positive integer>
-PworkbenchWorldgenWatershedTileCacheTiles=<positive integer>
-PworkbenchWorldgenJfrBiomePoints=true|false
```

Failures are always logged with their phase, chunk, exception, and stack trace.

## What has actually run

On August 4, 2026, the final production-remapped `0.4.0` jar ran on Java
`25.0.4` with Cleanroom `0.6.8-alpha`, BOP `7.0.1.2445`, GroovyScript `1.4.3`,
Cave Generator `1.2`, fixed seed `8675309`, and full diagnostics sampling. It
generated 648 chunks, populated 581, reached `Done` in 6.979 seconds, and shut
down cleanly with no prototype failure.

The run built six 80-by-80 extended watershed grids (38,400 cells), observed
11 river chunks, 14 stream chunks, and 39 chunks above the configured
depression-fill threshold. JFR observed the `sample.chunk` stage at 121
microseconds p50 and 247 microseconds p95; six tile builds observed 2,804
microseconds p50 and 15,308 microseconds maximum. The final run observed 24,159
tile-cache hits. In the preceding paired experiment, a per-window tile
lookaside reduced hits from 522,355 to 23,927 while all 642 generation and 585
population records remained semantically identical.

On August 4, 2026, the final production-remapped `0.3.1` jar generated a new
world with the same fixed seed, plan, and production-shaped mod set as the
retained `0.3.0` baseline. It generated 628 chunks, populated 577, reached
`Done` in 12.710 seconds, and shut down cleanly. After excluding elapsed time
and new cache telemetry, all 628 generation records and all 577 population
records matched the baseline exactly.

The run observed 628 computed and 628 reused chunk samples, 75,368 biome reads
from cached chunks, 97,371 exact point-cache hits, and 17,902 scalar biome
evaluations. An intermediate run without the point cache observed 115,273
scalar evaluations. Generation latency was 1,849 microseconds at nearest-rank
p50 and 2,802 microseconds at p95, compared with 2,279 and 3,154 in the one
retained baseline run. These are directional instrumented observations, not a
performance guarantee.

On August 3, 2026, the production-remapped `0.3.0` jar launched a fresh,
fixed-seed `wb_proto` dedicated-server world with Cleanroom `0.6.8-alpha`, BOP
`7.0.1.2445`, GroovyScript `1.4.3`, and Cave Generator `1.2`. GroovyScript
published plan version `2` with hash `f7f4b559368970b0`. The constructed
generator reported Cave Generator's `EarlyCaveHook` for caves and
`NoGeneration` for the vanilla ravine slot.

The tuned smoke generated 628 chunks, populated 577, reached `Done` in 13.289
seconds, and shut down cleanly. Within that fixed spawn sample:

- 141 chunks contained river columns, including 40 full-channel chunks;
- 45 chunks contained stream columns and none were entirely stream;
- 515 chunks contained underground air after the selected carver stage;
- all 577 populated chunks invoked native biome decoration once; and
- the selected regional land biome was
  `biomesoplenty:coniferous_forest`, alongside river, frozen-river, and beach
  outcomes.

The single observed mega-region is expected at a 3,072-block region scale; a
spawn-radius smoke is too small to prove boundary blending or all three region
types. Logs, worlds, and artifacts remain under ignored `.workbench/` local
custody. This is direct development evidence, not a committed Crucible receipt
or general compatibility approval.

Raw production BOP and GroovyScript jars cannot simply be dropped into this
Gradle deobfuscated development runtime: their production access-transformer
and early-mixin assumptions are not reconstructed by a plain file dependency.
The exact integration smoke therefore used a production Cleanroom server. That
is a provisioning limitation of this fixture, not evidence against the
production mod combination.

## Current boundary

The current network solves halo-bounded drainage and exposes downhill
receivers, tributary accumulation, discharge, and stream order. It does not
yet exchange globally conserved flux between macro tiles; each extended tile
still treats its outer halo edge as an outlet. Fill depth is diagnostic and
does not yet render lake shorelines. Lithology now drives permeability and
erodibility, but strata, contacts, faults, sediment feedback, aquifers, and
ore-host relationships remain future work.

The slice owns no structures, lakes, dungeons, or structure-query state. It
does not yet have a client visual smoke or an RC integration run. Those are
visible next steps, not prerequisites for iterating this generator now.

The adjacent [`worldgen-prototype-pattern-v1.json`](../worldgen-prototype-pattern-v1.json)
remains the verbatim identity-bearing snapshot of the initial flat scaffold.
Do not present that V1 identity as the identity of this evolved `0.4.0` slice.
