# GTCEu Subsurface Studio

GTCEu Subsurface Studio is Workbench's terminal-first, read-only subsurface
debugger. It composes exact GTCEu definition inventories, version-bounded pack
knowledge, validated Strata final-state shards, and optional controlled ore
decision traces without turning any of them into a second authority.

The V1 commands are exposed through `workbench subsurface` and
`workbench subsurface`. An explicit pack profile is always required.

```bash
python3 tools/workbench.py subsurface --profile supersymmetry summary
python3 tools/workbench.py subsurface --profile supersymmetry map \
  --layer ore-blocks --material cassiterite
python3 tools/workbench.py subsurface --profile supersymmetry explain \
  --position 1032 28 1037 --cave-radius 12
python3 tools/workbench.py subsurface --profile supersymmetry section \
  --x 1032 --min-y 0 --max-y 96
```

The Supersymmetry profile supplies defaults for the retained GTCEu 2.8.10
inventory, impact inventory, and 16×16 Strata region. Any input can be replaced
explicitly. Missing profile defaults are reported; they are never downloaded
or silently substituted.

## Developer questions

V1 answers practical bounded questions:

- Which ore, lithology, cave-void, biome, height, exposure, or virtual-fluid
  patterns occur per chunk?
- What exact final block occupies one coordinate, which host variant does it
  retain, and is it exposed to a subsurface void?
- Which exact GTCEu definitions can emit an observed material at this
  dimension and height?
- Why can an empty coordinate not yet be explained causally?
- What changed between aligned baseline and candidate captures?

Definition candidates are not deposit attribution. When a validated Crucible
subsurface trace is supplied, its deposit and per-position decision IDs are
joined to final state. Without that trace, the result remains explicitly
`candidate-only` even if one definition happens to be the sole material match.

## Layers and evidence

The chunk map supports `ore-blocks`, `ore-materials`, `surface-indicators`,
`lithology`, `subsurface-air`, `ore-exposure`, `height`, `biome`, and
`fluid-yield`.
With a validated controlled trace it also supports `ore-attempts`, including
per-chunk exact decision-outcome counts.
Ore blocks, block states, biomes, and virtual fluid cells are final-state
observations from Strata. Lithology is a pack-profile classification over
those exact states. Subsurface air is a presentation derivation: final air
below the exact captured height surface, not proof that a particular cave
generator carved it. Exposed ore faces are final spatial relationships, not
placement cause.

GTCEu bedrock fluids remain virtual persistent cells. They are never rendered
as physical underground fluid pockets.

## Boundaries

The Studio is a composition and navigation surface. Crucible validates
controlled traces and physical-capture receipts, Atlas interprets admitted
causal evidence, Strata owns final-state capture/rendering, and the
Supersymmetry profile owns GTCEu 2.8.10 dimension and host semantics. The
Studio does not launch Minecraft, edit configuration, run reload, infer an
unobserved selection, or approve terrain quality.

See the [V1 contract](contracts/gtceu-subsurface-studio-v1.md) and
[architecture](../../docs/architecture/GTCEU-SUBSURFACE-STUDIO.md).
