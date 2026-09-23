# Crucible GTCEu worldgen inventory V1

Status: experimental version-specific adapter for GregTech CEu 1.12.2
`2.8.10-beta`

## Purpose

The inventory binds the exact GTCEu jar and every selected world-generation
JSON, validates the jar's public worldgen API classes, normalizes configuration
parameters for analysis, and optionally correlates exact Strata resource block
states with candidate definitions by material token.

That correlation is intentionally non-causal. The same material can occur in
several deposits and host-stone variants do not identify the selected deposit.
Atlas/Observatory evidence is still required to say which definition caused a
specific block.

## Supported influence surfaces

The exact 2.8.10 sources expose these mod-level seams:

- `WorldGenRegistry.initializeRegistry()` installs
  `WorldGeneratorImpl.INSTANCE` through Forge at weight 1 and subscribes its
  vanilla-ore denial listener to `ORE_GEN_BUS`;
- `reinitializeRegisteredVeins()` reloads `config/gregtech/worldgen/vein` and
  `fluid`, clears the registry's ore biome/dimension cache, and rebuilds its
  public definition lists;
- `addVeinDefinitions`, `removeVeinDefinitions`, and the three component
  factory registrars support addon definitions and custom shape, filler, and
  populator implementations when called at the correct lifecycle phase;
- ore selection uses deterministic 3×3-chunk grids through `CachedGridEntry`;
  and
- bedrock fluids use deterministic cached 8×8-chunk cells through
  `BedrockFluidVeinHandler`.

JSON remains the lowest-risk pack customization seam. A GroovyScript adapter
can call the public registry methods, but it must register component factories
before parsing dependent definitions. Do not treat
`reinitializeRegisteredVeins` as complete cache invalidation: the additive
[V2 impact inventory](gtceu-worldgen-impact-inventory-v2.md) documents retained
ore-grid and bedrock-fluid state in this exact version.

## Observe and quantify

```bash
python3 modules/crucible/tools/inventory_gtceu_worldgen.py \
  --jar <runtime>/mods/gregtech-1.12.2-2.8.10-beta.jar \
  --config-root <runtime>/config/gregtech \
  --strataview <exact-package>.strataview \
  --out .workbench/evidence/gtceu/<label>/inventory.json
```

The report includes definition counts, positive/nonpositive weights, height and
density extents, generator/filler/populator frequencies, explicit dimensions,
material tokens, exact observed GTCEu state counts, and candidate definition
paths.

## Modify safely

The overlay format supports `add`, `replace`, and `remove` operations. Replace
and remove operations require the exact pre-change SHA-256. Before creating an
output, the materializer re-inventories the supplied jar and full configuration
tree and rejects any drift from the named source inventory. It then copies only
the GTCEu worldgen configuration into a fresh ignored destination, applies the
checked operations there, and inventories the result. It never edits the
supplied pack configuration and never reloads a live server:

```bash
python3 modules/crucible/tools/materialize_gtceu_worldgen_overlay.py \
  --jar <gregtech.jar> \
  --config-root <source>/config/gregtech \
  --inventory .workbench/evidence/gtceu/<label>/inventory.json \
  --plan <overlay.json> \
  --out-config-root .workbench/overlays/<label>/config/gregtech
```

Deployment and restart remain explicit later actions because they change
runtime state. The built-in reload command is not a safe general hot-reload
boundary for this version. New, previously unqueried chunks in a fresh
disposable world/save must be used to observe changed generation; existing
placed ore blocks are not retroactively rewritten.

Machine-readable forms:

- [`gtceu-worldgen-inventory-v1.schema.json`](../schemas/gtceu-worldgen-inventory-v1.schema.json)
- [`gtceu-worldgen-overlay-v1.schema.json`](../schemas/gtceu-worldgen-overlay-v1.schema.json)
- [`gtceu-worldgen-overlay-materialization-v1.schema.json`](../schemas/gtceu-worldgen-overlay-materialization-v1.schema.json)
