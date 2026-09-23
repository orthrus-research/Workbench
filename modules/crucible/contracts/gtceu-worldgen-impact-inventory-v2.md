# Crucible GTCEu worldgen impact inventory V2

Status: experimental version-specific adapter for Minecraft 1.12.2 and
GregTech CEu `2.8.10-beta`

## Purpose

V2 composes, rather than redefines, the V1 definition inventory. It answers a
larger question: every bounded mechanism by which this exact GTCEu runtime can
alter terrain, vegetation, vanilla ore events, virtual bedrock resources,
structure loot, or generation state, plus installed jars and Groovy scripts
that reference those mechanisms.

The source case study is upstream tag [`v2.8.10`](https://github.com/GregTechCEu/GregTech/tree/v2.8.10),
commit `9fe140febe8747bbe2f06dfd570421331ec06f4b`. The report binds the actual
runtime jar and all relevant class entries; the source tag explains those
bytes but does not replace them as runtime identity.

## Contents

The inventory includes:

- the complete validated V1 ore/fluid definition inventory;
- all nine `gregtech.cfg` worldgen controls and their immediate implications;
- every ore definition's selection, height, density, shape, filler, predicate,
  biome, dimension, and populator controls;
- the complete bedrock-fluid table, including yield and depletion semantics;
- Forge entry points, all built-in component factories, the public registry and
  bedrock-fluid methods useful to an adapter, and their safe lifecycle phases;
- exact spatial/cache constants plus an explicit boundary that keeps dynamic
  Groovy execution out of chunk, block, and cell-query hot paths;
- a twelve-effect catalog separating block writes, event denial, simulated
  maps, persistent state, loot mutation, conditional integration, and
  presentation-only behavior;
- the exact broader class surface used to derive that catalog;
- a cache and persistence matrix for registry reload;
- a bounded token scan of every installed mod jar and direct GTCEu-worldgen
  references in GroovyScript; and
- optional pack context for NoWorldgen5You, VisualOres, SussyPatches, and one
  retained debug log.

The jar scan produces candidates, not behavioral findings. Reflection and
generated or indirect integrations can evade it, while a constant-pool match
can be unreachable or observational only. Semantic review and runtime evidence
remain separate steps.

## Reload boundary

`/gregtech worldgen reload` directly calls only
`WorldGenRegistry.reinitializeRegisteredVeins()`. In this exact source it
clears the public ore/fluid definition lists and the registry's biome/dimension
ore cache. It does not clear `CachedGridEntry.gridEntryCache`,
`BedrockFluidVeinHandler.veinList`, `totalWeightMap`, or `veinCache`, and it
does not call `recalculateChances`.

Accordingly, V2 marks general hot reload unsafe and source-derived, not yet
runtime-proven. Workbench should use fresh materialized configuration, a full
restart, and a fresh disposable world/save for controlled ore and fluid-map
experiments until a version-specific invalidation patch passes an exact probe.

## Run

```bash
python3 modules/crucible/tools/inventory_gtceu_worldgen_impact.py \
  --jar <runtime>/mods/gregtech-1.12.2-2.8.10-beta.jar \
  --config-root <runtime>/config/gregtech \
  --runtime-root <runtime> \
  --strataview <exact-package>.strataview \
  --out .workbench/evidence/gtceu/<label>/gtceu-worldgen-impact-inventory-v2.json
```

The output is content-addressed and must remain under ignored `.workbench`
custody. It performs no deployment, reload, world creation, block mutation, or
Recurrent Complex coordination.

Machine-readable form:
[`gtceu-worldgen-impact-inventory-v2.schema.json`](../schemas/gtceu-worldgen-impact-inventory-v2.schema.json).
