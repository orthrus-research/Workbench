# Inspect and stage GTCEu worldgen changes

Status: experimental working guide

Use this for a Minecraft 1.12.2 / GTCEu 2.8.10 runtime when an overhaul must
preserve, constrain, observe, or deliberately replace ore and bedrock-fluid
behavior.

## Authority boundary

The exact jar, configuration, installed mod tree, and launch log define the
runtime input. Strata supplies bounded final block state. Crucible inventories
and stages changes; Atlas interprets admitted causal evidence. A matching
material token is a candidate relationship, not a placement proof.

## Inventory the runtime

```bash
python3 modules/crucible/tools/inventory_gtceu_worldgen_impact.py \
  --jar <runtime>/mods/gregtech.jar \
  --config-root <runtime>/config/gregtech \
  --runtime-root <runtime> \
  --out .workbench/evidence/gtceu/<label>/impact-v2.json
```

Review definition controls, global controls, effect categories, public API
seams, reload/persistence hazards, installed integration candidates, and any
bound Strata observations separately. Candidate jar matches require source or
bytecode review before assigning behavior.

Keep physical ore generation separate from virtual bedrock-fluid cells. Fluid
cells are persistent, lazily initialized data; querying an unseen cell may
create it. Record whether a cell existed before observation.

## Choose the implementation boundary

Use a declarative Groovy plan for authoring, validation, registry resolution,
and startup-time publication. Freeze a typed Java snapshot before world
construction. Java owns chunk, block, RNG, and fluid-cell hot paths.

Do not use the built-in reload command for controlled before/after comparison:
it does not invalidate every ore and bedrock-fluid cache or saved cell. Use a
full restart and a fresh world.

## Stage a checked overlay

Use JSON overlays for supported definition add, replace, or remove operations.
Bind replacements and removals to the exact source-file digest, then
materialize into fresh ignored storage:

```bash
python3 modules/crucible/tools/materialize_gtceu_worldgen_overlay.py \
  --jar <gregtech.jar> \
  --config-root <source>/config/gregtech \
  --inventory .workbench/evidence/gtceu/<label>/inventory.json \
  --plan <overlay.json> \
  --out-config-root .workbench/overlays/<label>/config/gregtech
```

The tool does not deploy, restart, reload, or create a world. Use a Java
adapter when the change needs a new selection model, geometry, filler,
populator, ore grid, or fluid-cell model.

Compare baseline and candidate with the same seed, dimension, world type, mod
set, and aligned fresh-world window. Record selected definitions, spatial
identity, RNG, cache state, candidate/write counts, host blocks, fluid yield,
and final Strata state without collapsing them into one conclusion.
