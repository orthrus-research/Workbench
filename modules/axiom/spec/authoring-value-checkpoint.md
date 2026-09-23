# From native content qualification to material-authoring feedback

Historical design checkpoint. Its recommended material preflight now exists in
bounded form; this is retained rationale, not the current backlog. Follow the
[initialization-check MVP](initialization-mvp.md) for current completion criteria
and [material preflight](material-program-preflight.md) for the implemented workflow.

This checkpoint compares Axiom's bounded native capabilities with the selected
Supersymmetry source: pack `3e83cd7bad57bd4c424de4e6cc707ab02fe32f54` and
Susy-Core `2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a`. These are source
observations, not a current-upstream survey or an executed complete pack.

## What developers actually edit

| Authoring surface | Source-observed workflow | Useful feedback |
| --- | --- | --- |
| Material declarations | `groovy/material/OreMaterials.groovy` uses material builders, explicit IDs, `.dust().ore()` / `.gem().ore()`, flags and compositions. Some declarations also request Susy slurry fluids. | Registration errors, property/flag outcomes, expected generated forms and unresolved references. |
| Fluid/property changes | `groovy/classes/ChangeFlags.groovy` changes existing materials, calls `setupFluidTypes`, queues plasma registration and uses Susy storage keys. `ThirdDegreeMaterials.groovy` uses `material(...)`, amount multiplication and acidic/basic builders. | Exact fluid identity, storage-key dependencies, mutation timing and missing context. |
| Custom items | `groovy/preInit/RegisterMetaItems.groovy` listens to `PostMaterialEvent`, constructs `StandardMetaItem(2 as short)`, uses explicit IDs/gaps and adds behavior components. | Metadata/name conflicts and whether the intended item exists; unsupported behavior components must remain explicit. |

`groovy/preInit/MaterialChanges.groovy` registers a **LOWEST-priority material
event listener**. It admits Susy's FIBER base property, invokes
`material.SuSyMaterials.init()`, then `ChangeFlags.init()`. The material aggregator
calls eleven producer groups in source order before changing formulas. Registering
an isolated builder is not equivalent to executing this sequence.

The custom-item listener runs in the post-material phase, before the existing
generated-prefix checkpoint's fresh-item assumptions. Its integration needs
explicit shared ownership, not a second item universe or a fake late replay.

## Assessment at this checkpoint

The native material/property/registry work and generated item/block/ore families
are useful foundations. Ore qualification specifically exposes cases where
generation, ore membership and unification disagree. However, family counts and
registration receipts are not yet developer-facing source validation.

The nearest missing connection was **source → actual native declaration →
source-located result** in a declared lifecycle context. Completing every block
family first would not supply that connection. Surface rocks and fluid blocks
remain valid future work, but are not automatically the next priority.

## Historical recommendation

Build a bounded **native material-authoring preflight** with representative pack
syntax and source locations. Start with basic material declarations whose
dependencies are qualified; preserve their real builder/property/registration
behavior and return both native errors and expected-form results. Each result
must identify the source, target revisions, lifecycle phase and admitted context.

Qualify the actual Groovy source/mapping/event dependencies before claiming script
execution. Do not substitute a regex-only linter or translate recipes into a
handwritten material simulator. Missing addon properties, storage keys, component
references or listener composition must yield incomplete coverage, not a pass.

Acceptance should demonstrate a developer edit that produces a source-located
native failure, a valid edit that produces the expected registered form, and an
unqualified dependency that explicitly refuses a validity conclusion. The useful
output belongs in existing Review → Change → Diagnose flows, with IDE-led edits;
no new studio, generated blueprint workflow or whole-machine validity claim is
needed. Subsequent work should close fluid/property and custom-item authoring
gaps according to these representative edits rather than class-count targets.
