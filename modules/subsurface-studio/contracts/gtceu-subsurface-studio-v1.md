# GTCEu Subsurface Studio V1

Status: experimental read-only composition contract

Machine-readable artifacts:

- [result schema V1](../schemas/workbench-subsurface-studio-result-v1.schema.json)
- [pack profile schema V1](../schemas/workbench-subsurface-profile-v1.schema.json)

## Purpose and authority

V1 presents one exact, bounded subsurface dataset through summary, layer,
chunk-map, coordinate-explanation, vertical-section, definition, fluid-cell,
and aligned-comparison operations. It creates no mechanical fact.

- Crucible owns validated GTCEu inventories and controlled decision traces.
- Strata owns exact captured final block, biome, and virtual-fluid state.
- Atlas owns causal interpretation of admitted runtime evidence.
- A pack profile owns version-specific dimension, grid, host, and block-state
  semantics.
- Workbench Subsurface Studio validates, correlates, derives presentation
  metrics, and navigates among those sources.

Every result retains source identities, state, scope, uncertainty, and
limitations. A static definition is not upgraded to an observed selection, and
a final ore block is not attributed to a deposit from material spelling.

## Inputs

The required inputs are:

1. one explicit `workbench-subsurface-pack-profile-v1`;
2. one content-addressed Crucible GTCEu worldgen inventory V1; and
3. one Strata region manifest V2 with its exact sharded tiles.

A GTCEu impact inventory V2 is required for full definition controls and may
be omitted only when the operation can remain visibly partial. A Crucible
GTCEu subsurface trace V1 is optional and is the only V1 source that may bind a
position to a deposit instance or observed rejection decision.

Profile defaults are resolved only after the caller explicitly selects the
pack. Inputs must be regular non-symlink files. JSON is bounded, read with
replacement detection, and never evaluated as code. Tile paths must remain
below the manifest directory; duplicate windows, sections, voxels, or coverage
are rejected.

## Correlation

The Studio requires the inventory observation and Strata manifest to declare
the same chunk window. Their complete GTCEu block-state counts must agree.
This closes final-state correlation for the submitted shards; it does not
retroactively make the historical manifest a Crucible receipt or prove which
definition caused a block.

The impact inventory must embed the same exact GTCEu adapter, artifact,
configuration tree, and definitions. Its embedded inventory ID may differ only
because that impact capture retained another historical Strata observation;
the Studio reports that scope difference and uses the separately supplied
inventory for current final-state correlation.

An optional trace must match the profile, GTCEu inventory ID, world seed,
dimension, and a containing chunk scope. Trace deposit definitions must exist
in the exact inventory. Per-position decisions may join only at their exact
coordinate and deposit instance.

## Operations

- `summary` reports exact source/scope health, aggregate ore, material, host,
  final subsurface-air, exposed-face, and fluid-cell counts.
- `layers` reports availability, evidence state, derivation, and limitations
  for every V1 layer.
- `map` returns a deterministic row-major chunk grid for one layer, optionally
  filtered to one material. `ore-attempts` is admitted only when a controlled
  trace supplies exact per-position outcomes and is never inferred from final
  ore blocks.
- `explain` returns the exact final block, biome, height, lithology, six
  neighbors, bounded nearest subsurface air, grid context, definition
  candidates, and any exact trace decision.
- `section` returns a bounded vertical plane with exact categorical cells and
  run-length columns suitable for terminal or graphical clients.
- `definitions` returns exact version-bound ore definitions and observed
  material counts without claiming selection.
- `fluids` returns virtual GTCEu bedrock-fluid cells separately from physical
  blocks.
- `compare` requires identical seed, dimension, and chunk window and reports
  definition, per-material, per-host, per-chunk, cave-context, and fluid-cell
  differences. It is a final-state comparison, not a causal verdict.

## Evidence states

V1 uses these states:

- `declared`: exact configuration or versioned profile declaration;
- `observed-final`: exact Strata final state;
- `observed-controlled`: validated Crucible decision trace;
- `derived-presentation`: deterministic classification or spatial metric over
  exact final state;
- `candidate-only`: exact definitions share a material/dimension/height
  relation but selection is unobserved; and
- `unavailable`: required coverage is absent.

`subsurface-air` means final air below the exact captured height surface in a
captured dense section. It does not identify the responsible carver. Ore
exposure means an exact final ore face borders air; it does not prove when the
air was created.

## Result identity and validation

The output format is `workbench-gtceu-subsurface-studio-result-v1` with schema
version `1`, `read_only: true`, and a content-addressed `result_id`. The
semantic validator recomputes the ID, checks unique and exact source bindings,
operation/status consistency, scope, evidence states, map dimensions,
coordinate bounds, and uncertainty requirements. Content addressing detects
result mutation; it does not authorize forged upstream evidence.

## Safety and scale

V1 accepts at most 1,024 chunks, 256 tiles, 512 MiB of tiles, 100,000 palette
states, 10,000 deposits, and 250,000 trace decisions. A vertical section is
limited to 256 columns and 256 vertical cells. Cave-distance search is limited
to 32 blocks. Bounds are rejected before expensive traversal when possible.

The command is read-only. It never launches Minecraft, loads JVM classes,
edits a world or configuration, runs GTCEu reload, writes an index, or starts
the external viewer. Existing checked Strata handoffs remain the rendering
route.
