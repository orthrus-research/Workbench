# Cleanroom 0.6.8-alpha world-generation influence surface

Status: exact-candidate audit supplement; not a stable platform promise

This document answers a construction question that the observation-oriented
V1 hook catalog does not: which public interfaces, registry operations,
virtual methods, Forge events, and invasive techniques can influence each
stage of Minecraft 1.12.2 world generation?

It is bound to the exact [candidate lock](candidate-lock-v1.json), the existing
[V1 catalog](worldgen-hook-catalog-v1.json), and its additive
[errata overlay](worldgen-hook-catalog-v1-errata-v1.json). V1 remains verbatim.
The overlay records corrections and missing supported surfaces rather than
silently changing its identity.

## The ownership model

A complete replacement is a stack of decisions, not a single event:

```text
WorldInfo world type / dimension id
  -> WorldType or WorldProvider
    -> BiomeProvider (climate and biome allocation)
    -> IChunkGenerator
       -> base density and blocks
       -> biome surface
       -> carvers and primer structures
       -> population and placed structures
       -> emitted Forge population/decorator/ore events
    -> patched Chunk dispatches registered IWorldGenerator callbacks
  -> chunk persistence, structure bookkeeping, spawn queries, saves
```

The supported top-level construction seam is therefore an explicit
`WorldType`/`WorldProvider` selecting a custom `BiomeProvider` and a complete
seven-method `IChunkGenerator`. Forge does not automatically inject all
terrain events into a custom implementation. The implementation must call the
compatibility helpers it promises to deliver.

## Construction surface by stage

| Stage | Supported influence surface | Delivery and limits |
|---|---|---|
| Registration | `RegistryEvent.Register<Biome>`, `ForgeRegistries.BIOMES`, `BiomeDictionary.addTypes`, `BiomeManager` pools/lists | Biome ID limit is 255. Register before dictionary tagging. Configure lists before consumers snapshot them. |
| World selection | Construct a `WorldType`; override `getBiomeProvider`, `getChunkGenerator`, `getBiomeLayer`, `getMinimumSpawnHeight`, and `getSpawnFuzz` | A world uses the persisted selected type. A custom dimension instead uses `DimensionType`, `DimensionManager`, and a `WorldProvider`. Replacing an existing dimension ID is global and ordering-sensitive. |
| Dimension provider | Override `WorldProvider.init`, `createChunkGenerator`, `canCoordinateBeSpawn`, and mandatory `getDimensionType` | Appropriate for a dimension owner. It is not a safe way for two mods to share ownership of the same dimension. |
| Biome allocation | Subclass through protected `BiomeProvider()` and implement inherited queries; build GenLayers or another deterministic allocator | A custom provider may call public `getModdedBiomeGenerators` to deliver `WorldTypeEvent.InitBiomeGens`. The private three-argument initializer is default-only. |
| Base chunk | Implement `IChunkGenerator.generateChunk`; write a `ChunkPrimer`, biome array, height/skylight data, and return a valid `Chunk` | This is full ownership. `ChunkGeneratorEvent.InitNoiseField` and `ReplaceBiomeBlocks` occur only when the implementation deliberately invokes their helpers. |
| Surface | Override `Biome.genTerrainBlocks`, own an equivalent surface pass, or honor `ForgeEventFactory.onReplaceBiomeBlocks` | Calling biome surface methods composes with biome implementations; replacing them gives the generator full control. Choose and document one owner. |
| Carvers and primer structures | Instantiate `MapGenBase`/`MapGenStructure`; use `TerrainGen.getModdedMapGen` for replaceable categories; call `generate` in a declared order | Forge replacement events are helper-driven. Structure starts also need persistence and recreation, not only block placement. |
| Population | Implement `IChunkGenerator.populate`; post `PopulateChunkEvent.Pre/Post`; gate named operations through `TerrainGen.populate` | Generator-emitted, not automatic. `BlockFalling.fallInstantly` and RNG restoration must be exception-safe. Do not hide cascading-generation diagnostics. |
| Biome decoration and ores | Call `Biome.decorate`, use/override `BiomeDecorator`, or own the stages while posting `DecorateBiomeEvent` and `OreGenEvent` through the correct buses | `BiomeDecorator.decorate` is public; `genDecorations` and `generateOres` are protected subclass seams. `TerrainGen.decorate/generateOre` emit per-feature decisions only when called. |
| Independent late generation | `GameRegistry.registerWorldGenerator` plus `IWorldGenerator.generate` | Forge's patched protected `Chunk.populate(IChunkGenerator)` calls `GameRegistry.generateWorld` after generator population and resets the Forge RNG for each callback. A custom generator must not redispatch it. |
| Creature spawning | `IChunkGenerator.getPossibleCreatures`, biome spawn lists, `WorldEntitySpawner.performWorldGenSpawning`, and `WorldEvent.PotentialSpawns` | Generation-time spawning is generator-owned. Runtime potential-spawn additions have an identity-sensitive reuse rule. |
| Structure queries | Implement `getNearestStructurePos`, `isInsideStructure`, `generateStructures`, and `recreateStructures` | Returning placeholders is valid only while the prototype declares itself structure-free. Production structures need query and reload semantics. |
| Persistence/lifecycle | `ChunkDataEvent.Load/Save`, `ChunkEvent.Load/Unload`, `WorldEvent.Load/Save/Unload` | These observe/extend persisted data and lifecycle; they are not substitutes for generator ownership. Client and server callsites differ. |

## Terrain event delivery modes

There are three materially different mechanisms:

1. **Framework automatic:** Forge's patched `Chunk` invokes registered
   `IWorldGenerator`s after the active generator returns. Mods participate by
   registration and callback, not by calling `GameRegistry.generateWorld`.
2. **Generator opt-in:** `InitMapGen`, `InitNoiseGens`, `ReplaceBiomeBlocks`,
   `Populate`, `Decorate`, `OreGen`, and many `TerrainGen` decisions appear
   only when the selected generator or decorator calls/posts them.
3. **Default-implementation only:** hooks patched into vanilla
   `BiomeProvider` or vanilla chunk generators do not magically occur after a
   custom implementation bypasses those classes.

That distinction is the compatibility contract for the replacement mod. For
each emitted event it must specify bus, event type, position convention, RNG
instance/seed, ordering, cancellation/result semantics, and behavior when a
listener throws.

## Supported, invasive, and observational techniques

| Classification | Examples | Workbench policy |
|---|---|---|
| Supported construction | Registries, `WorldType`, `WorldProvider`, `BiomeProvider`, `IChunkGenerator`, biome/decorator overrides, `IWorldGenerator` registration | Preferred for the production design. Bind exact candidate signatures. |
| Supported compatibility events | Forge event subscribers and explicit `TerrainGen`/`ForgeEventFactory` calls | Use when the owning implementation deliberately promises the lifecycle. Test event order and third-party behavior. |
| Invasive global mutation | Unregister/re-register dimension `-1`, replace vanilla globals, reflect private `BiomeManager` methods | Treat as conflict-prone profile-specific intervention, never a generic default. |
| Invasive implementation | Access transformers, Mixins, reflection, copied vanilla generators | Allowed only with exact target evidence, transformation observability, and a declared reason no supported seam works. |
| Invasive observation | Probe `ChunkProviderServer`, `MapGenBase`, `Biome.decorate`, block-write hot paths, event listener invocation, biome-array writes | Observer behavior only. These probes say what happened; they do not authorize construction. |

## Known blockers and timing traps

- `MapGenStructureIO.registerStructure` is private and
  `registerStructureComponent` is package-private in candidate-derived
  bytecode. Vanilla-format persistence for a new structure family has no
  supported public registration seam. Use an explicitly invasive access path
  or own a separate persistence design, and keep this unresolved until tested.
- `GenLayerBiome`, the default `BiomeProvider`, and `MapGenStronghold` snapshot
  different compatibility lists at construction. Late mutation can appear to
  succeed while leaving existing objects unchanged.
- `BiomeManager.addVillageBiome(Biome, boolean)` ignores the boolean and always
  adds the biome in this exact candidate.
- A block-write trace cannot prove biome ownership. Record the returned
  chunk's biome array and selected provider outputs as first-class evidence.
- V1 leaves eight generated Minecraft sources unresolved. A provisional
  derived jar confirms important descriptors/access flags, but it is not
  authority until its inputs and transformation recipe are locked.

## Observation contract

The observatory records these checkpoints separately:

- selected world type, provider, generator, dimension, seed, and generator
  settings;
- provider biome outputs and returned chunk biome array;
- entry/exit/failure for base generation, surface, each carver, each structure,
  population, decoration, ores, spawning, registered world generators, and
  persistence reconstruction;
- stable RNG-lane seeds without consuming runtime `Random` instances;
- listener owner/method/order and result or cancellation changes;
- block writes correlated to stage, actor, chunk, position, before/after state;
- chunk digests after meaningful boundaries and first divergence against a
  same-seed baseline;
- cascading chunk requests and requested-neighbor coordinates, never globally
  suppressed.

The buildable [prototype fixture](worldgen-prototype-fixture/README.md) now
starts this loop with playable deterministic terrain, one editable feature,
explicit Forge lifecycle delivery, and sampled JSON-line diagnostics. A direct
fresh-world and saved-world dedicated-server smoke has run successfully; no
formal Crucible receipt or third-party compatibility claim follows from that
local evidence. Its adjacent
[pattern asset](worldgen-prototype-pattern-v1.json) remains the verbatim,
identity-bearing snapshot of the initial flat scaffold rather than a current
source closure. The implementation is intentionally smaller than this surface
map.

## Catalog record requirements

Each catalog row separates construction and observation roles and records the
exact owner, name, descriptor, access, caller-to-callee evidence, delivery
mode, side and dimension scope, ordering, and source-or-artifact provenance.
Catalog generation fails when a claimed subclass seam is private or a claimed
call edge is absent.
