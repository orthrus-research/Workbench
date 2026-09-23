# GTCEu Subsurface Studio V1

GTCEu Subsurface Studio is a read-only terminal surface for correlating exact
GTCEu definitions, controlled trace evidence, and captured final world state.
It answers bounded questions about ore, lithology, cave space, biome, height,
exposure, and virtual fluid assignments without inventing generation cause.

The generic implementation is under
[`modules/subsurface-studio/`](../../modules/subsurface-studio/README.md).
GTCEu and Supersymmetry semantics come from an explicitly selected pack
profile; they are not built into the module.

## Commands

The direct and nested routes are equivalent:

```bash
workbench subsurface --profile supersymmetry summary
workbench subsurface --profile supersymmetry map --layer ore-blocks
workbench subsurface --profile supersymmetry explain --position 1024 1 1024
workbench subsurface --profile supersymmetry section --x 1032 --min-y 0 --max-y 96
workbench subsurface --profile supersymmetry definitions --material galena
workbench subsurface --profile supersymmetry --manifest CANDIDATE compare \
  --baseline-manifest BASELINE
```

The console exposes the same parser and composes argument vectors without a
shell. Use command `--help` for exact options.

## Composition and authority

```text
selected pack profile ------- version, dimension, grid, host semantics
Crucible inventories -------- exact GTCEu definitions and derived controls
Strata manifest/shards ------ captured final blocks, biomes, air, height
optional Crucible trace ----- controlled deposit and position decisions
              |
              v
Subsurface validation, correlation, bounded metrics, and presentation
```

- Strata owns captured final block, biome, height, terrain-face, and virtual
  fluid-cell state.
- Crucible owns validation of exact declarations and controlled trace records.
- The pack profile owns GTCEu version, dimensions, grids, and block/host
  classification.
- Atlas owns causal interpretation of admitted runtime evidence.
- Subsurface Studio correlates and presents; it creates no second truth.

A matching material token is only a candidate attribution. A final ore block
becomes an observed controlled deposit attribution only when an exact trace
decision at that coordinate wrote the same state. Complete coverage can close
the selected trace question; it cannot prove that no unrelated actor touched
the position.

## Input validation

The selected profile, GTCEu inventory, and Strata V2 manifest are required.
Every shard is validated before use. The Studio rejects unsafe paths and
links, replacement during read, duplicate or overlapping coverage, malformed
palettes and sections, sparse/dense disagreement, summary drift, and virtual
fluid inconsistency.

The inventory and final-state capture must cover the same chunk window and
agree on complete GTCEu resource-state counts. The impact inventory binds the
same artifact, configuration tree, and definition surface. Any historical
observation with a different scope remains an explicit limitation.

An optional trace must match profile, inventories, seed, dimension, and a
containing chunk window. Its definition references, counts, decisions,
coverage claim, and content identity are validated. The trace assembler under
`modules/crucible/tools/` validates already observed records; it neither
instruments nor launches Minecraft.

## Operations

| Operation | Question | Boundary |
| --- | --- | --- |
| `summary` | What dominant patterns and coverage exist? | Selected capture only |
| `layers` | Which views are available and why? | Missing evidence stays unavailable |
| `map` | How do bounded chunk metrics vary? | Deterministic cells and legends |
| `explain` | What is at one coordinate and what surrounds it? | Cause only from exact controlled write |
| `section` | How do ore, host stone, and final void align vertically? | Bounded exact cells |
| `definitions` | Which declarations can emit a material? | Declared controls; selection unobserved |
| `fluids` | Which virtual fluid assignment applies? | Never rendered as a physical pocket |
| `compare` | What differs between aligned captures? | Difference is not causation |

Map layers include ore blocks/materials, surface indicators, lithology,
subsurface air, exposure, height, biome, and fluid yield. Trace-only attempt
layers are never backfilled from final ore blocks. `subsurface-air` means a
profile-declared final air state below the captured surface; it does not name
the carver that produced it.

## Results and safety

JSON output uses `workbench-gtceu-subsurface-studio-result-v1`. A result is
content-addressed, read-only, profile- and scope-bound, and retains source
identities, evidence states, uncertainty, limitations, and navigation. The
validator recomputes its identity and operation-specific bounds.

Terminal rendering sanitizes every upstream string and exposes source
bindings on request. Repository tests use synthetic fixtures; validation does
not depend on ignored local world captures.

The Studio does not edit definitions, materialize overlays, reload worlds,
approve changes, infer a deposit from a final block alone, assign a carver
from final air, or turn a baseline/candidate delta into a causal verdict.
Those stronger claims require their owning construction or runtime evidence
path.
