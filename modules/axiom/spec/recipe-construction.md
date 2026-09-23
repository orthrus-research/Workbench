# Recipe construction from source

Status: target specification, grounded in the pinned source baseline. The
[first implementation](first-slice.md) admits a narrower source program.
The [registration-input inspection](registration-inputs.md) identifies actual
binary handler declarations and the source-traced material-event sequence;
discovery is not execution of those handlers.

## Required input scope

Support the actual recipe-producing paths of the selected Supersymmetry source:
Groovy scripts, helper classes/functions and loops, Java registration, material
generation, builder callbacks, removals/replacements, custom recipe maps and
machine-specific dynamic construction.

A raw file is not an isolated complete program. A source check identifies the
baseline plus candidate file bytes, imports, relevant globals and configuration.
Files changed or removed must affect the same dependency graph. Parsing builder
syntax alone cannot certify effective recipe validity.

## Actual pack entry points

The pack's [groovy/runConfig.json](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/runConfig.json) assigns:

- `preInit`: `classes/`, `globals/`, `material/`, then `preInit/`.
- `postInit`: `prePostInit/`, then `postInit/`.

GroovyScript 1.4.3's `SandboxData.getSortedFilesOf` sorts paths within each group,
retains group order and moves repeated more-specific entries last. This does
not itself reproduce class compilation, imports or reload semantics. The first
implementation accepts an explicit file sequence, not the full loader program.
The run-config `version: 1.0.0` is not the pack release version.

[MetaClassExpansions.groovy](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/preInit/MetaClassExpansions.groovy) installs
dynamic RecipeMap field setters. [ModifyRecipeMaps.groovy](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/prePostInit/ModifyRecipeMaps.groovy)
then changes map shapes and removes registrations. Stock Java map capacities
alone are not the effective pack capacities.

[Recipemaps.groovy](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/prePostInit/Recipemaps.groovy) supplies aliases such as
MIXER, BLENDER and DT. Resolve those aliases to the effective map identity; do not
treat a variable name as an independent machine or registry.

The pack's [GroovyScript configuration](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/config/groovyscript.cfg)
also declares construction policy, including input-stack-count checking. The
scope of each setting must follow the actual compatibility implementation rather
than being generalized from its comment.

## Execution requirements

Use the upstream recipe-building operations where independently reusable.
Extract interleaved operations with an audited mapping of original types,
numeric behavior, registry access and callbacks. Do not substitute a permissive
recording DSL and call its results game-equivalent.

Preserve compilation and definition side effects inside isolated domain state:
registry creation and lookup, meta-class changes, material/ore flags, phase
ordering, callbacks, build validation, registration results, tree compilation,
and subsequent removals. A defined recipe can fail to remain registered or
discoverable.

Arbitrary Groovy/Java is not guaranteed statically decidable. Unsupported
dependencies, inaccessible state, reflection and evaluation limits must remain
visible. Axiom must not execute unknown code with unrestricted host privileges
or invoke Minecraft to resolve a missing dependency.

Each future input path must be classified as discovered, source-traced,
implemented, or qualified. Explicitly account for non-GT recipes and custom
processors; a generic GT builder is not universal coverage.

## Effective definitions and lineage

Preserve the path from source bytes and location through helper invocations,
parameter bindings, builder defaults, material generation and callbacks to each
effective recipe. Keep registration/removal order, map identity, ingredient
order, typed properties and relevant diagnostic events.

Distinguish: syntax/type acceptance; builder acceptance; registration acceptance;
tree reachability; final membership; and machine processing. Warn about intent
separately from whether the actual upstream accepts a definition.

Source identity must include candidate overlays and transitive dependencies,
not just the baseline Git commit. A cached answer must not survive a changed
helper, map shape, registry, mixin or configuration on which it depends.

## Initial source-backed cases

| Case | Actual source and implication |
| --- | --- |
| Ordinary builder | [Coolants.groovy](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/postInit/chemistry/organic_chemistry/Coolants.groovy): MIXER and BLENDER declarations; quantities must be checked against concrete machine handlers |
| Helper generation | [Photoresists.groovy](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/globals/Photoresists.groovy#L30-L56): a loop constructs multiple recipes, with circuit and cleanroom behavior |
| Circuit alternatives | [ChemistryOverhaul.groovy](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/postInit/chemistry/ChemistryOverhaul.groovy#L129-L145): configurations 1 and 2 select distinct BCR declarations |
| Ordered generated variants | [AcidAnhydrides.groovy](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/postInit/chemistry/organic_chemistry/carboxyl_derivatives/AcidAnhydrides.groovy#L70-L80): ordered tower outputs feed [UniversalDistillationRecipeBuilder](https://github.com/GregTechCEu/GregTech/blob/9fe140febe8747bbe2f06dfd570421331ec06f4b/src/main/java/gregtech/api/recipes/builders/UniversalDistillationRecipeBuilder.java) when that effective builder applies |
| Cross-map derivation | [SuSyRecipeMaps.java](https://github.com/SymmetricDevs/Susy-Core/blob/2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a/src/main/java/supersymmetry/api/recipes/SuSyRecipeMaps.java#L550-L555): MIXER construction copies a recipe into BLENDER |
| Material generation | [ChangeFlags.groovy](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/classes/ChangeFlags.groovy): flags and material bootstrap belong to construction dependencies, not recipe-text-only analysis |

These are fixture entry points, not assertions that the complete surrounding
program has already been compiled or validated by Axiom.
