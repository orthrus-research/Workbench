# Supersymmetry GTCEu material-classification policy v1

This profile supplies the GTCEu-specific authority consumed by Atlas's
[runtime material-classification contract](../../../../modules/atlas/contracts/atlas-runtime-material-classification-v1.md).

## Exact scope

- Minecraft: `1.12.2`
- GTCEu release: `2.8.10-beta`
- upstream tag: `v2.8.10`
- source commit: `9fe140febe8747bbe2f06dfd570421331ec06f4b`
- runtime material adapter: `gt_materials`
- policy ID:
  `workbench://profiles/supersymmetry/atlas/material-classification/gtceu-2.8.10-v1`

The immutable source mapping is recorded in
[`source-lock.json`](../source-locks/legacy-forge/source-lock.json). This policy
does not claim that modern GTCEu, another 1.12.2 fork, or a future Cleanroom
implementation has the same property closure.

## Property authority

The base-property set is `dust`, `empty`, `fluid`, `gem`, and `ingot`.
Capability properties are `blast`, `fluid_pipe`, `item_pipe`, `ore`, `polymer`,
`rotor`, `tool`, `wire`, and `wood`. Any other observed key is retained as a
profile extension; the current historical extractor has exact encoders for
the admitted Supersymmetry, Supercritical, Gregicality Multiblocks, and GTFO
extensions.

The policy records the verified GTCEu dependencies needed for comparison:

- ingot and gem require dust and are mutually incompatible;
- blast and rotor require ingot;
- ore, wire, and wood require dust;
- polymer requires both dust and ingot;
- tool requires either gem or ingot;
- fluid and item pipes require either ingot or wood and are mutually
  incompatible.

These are frozen-runtime expectations. A later Groovy checker must distinguish
an explicitly declared property from a dependency introduced during material
verification.

For source-side expected closure, the same policy also binds GTCEu's
deterministic fallback when an any-of requirement is absent: `fluid_pipe`,
`item_pipe`, and `tool` fall back to `ingot`. It also binds property-implied
flags: `wood` adds `flammable`, while `polymer` adds `flammable`,
`no_smashing`, and `disable_decomposition`. These values contribute to the
runtime policy digest; the source normalizer does not maintain a second
closure table.

## Flag authority

All GTCEu core flags are retained by exact name and classified into one or more
of `behavior`, `form_generation`, `process_policy`, and `restriction`. Addon or
pack flags that are not in the exact core table remain `profile_extension`.
That classification is navigational; the lossless flag name remains the
authority. The policy also binds each core flag's required flag closure and
required material properties so an incomplete or internally inconsistent
frozen flag set becomes an explicit frontier issue.

## Forms

The profile admits `ore_prefix_material_generation` constraints for the
eligibility lens. Actual item variants and fluids come only from observed
`has_form` relationships. In particular, `fluid` is a material property and a
fluid form is a separate registered relationship.

## Cleanroom transition

This is valid historical Forge evidence for Supersymmetry's pinned legacy
profile. A Cleanroom target must either demonstrate equivalent material
semantics under a separately admitted policy or publish the differences. This
policy must not be reused merely because class or property names look similar.

Pack Program Studio's bounded
[V2 material declaration normalizer](../../../../modules/pack-program-studio/contracts/material-declaration-normalizer-v2.md)
supplies declared-intent artifacts under the current composed semantic policy.
This historical classification contract remains available as evidence, but it
is not a supported predecessor declaration workflow. Crucible runtime capture
and Atlas comparison remain separate work.
