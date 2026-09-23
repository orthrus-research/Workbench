# Source baseline and reference policy

Axiom is constructed against primary upstream source, not remembered behavior,
display recipes or previously modified local test packs.

The [source lock](supersymmetry.lock.json) is the canonical identity record for
the bounded source model. It is not an implementation version, resolved JVM
dependency lock, installable pack manifest or declaration of runtime parity.

The [Cleanroom event lock](cleanroom-events.lock.json) retains source references
and selected native event-library identities for [native event execution](../spec/native-events.md). It does
not add Cleanroom implicitly to the pack baseline or qualify active transformations.
The former modeled executor has been retired; see
[retained findings](../spec/registration-research.md) and the
[profile-pinned native runtime](../spec/native-runtime.md).

The [native fluid lock](native-fluids.lock.json) separately binds the GTCEu,
Cleanroom and Susy-Core inputs for [isolated fluid construction](../spec/native-fluids.md).
It does not expand the public recipe endpoint or qualify pack bootstrap.
Its verifier reads immutable Git objects, validates retained source extraction,
and binds installed engine and source archives before native comparisons.

## Latest-source selection

The [material-prefix item lock](material-items.lock.json) binds the construction,
native registration and ore-membership excerpts described in
[material-items.md](../spec/material-items.md). Member-level retention is distinct
from whole-class or whole-loader qualification; other families remain explicit
future coverage.

The [material-block lock](material-blocks.lock.json) adds generated compressed
blocks, frames and ItemBlocks to that explicitly composed checkpoint. See
[material-blocks.md](../spec/material-blocks.md) for sparse metadata, native state
identity and qualification limits. This does not refresh the source target.

At `2026-09-09 02:20:01 UTC`, upstream HEAD resolved to:

- Repository: [SymmetricDevs/Supersymmetry](https://github.com/SymmetricDevs/Supersymmetry).
- Default branch: `master-ceu`.
- Commit: `3e83cd7bad57bd4c424de4e6cc707ab02fe32f54`.
- Git tree: `74330a55fff5af3a30222c071a13ca614938b93b`.
- Pack label: `0.1.16.15`, from [pack.toml](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/pack.toml).

“Latest” means the default branch at that resolution time, not latest release,
prerelease, pull-request head or the independent HEAD of every mod. Every
construction/validation run uses the immutable source composition, never a
floating branch. Subsequent upstream changes require an explicit refresh.

Pack-pinned source tags resolved to:

- [GTCEu v2.8.10](https://github.com/GregTechCEu/GregTech/tree/9fe140febe8747bbe2f06dfd570421331ec06f4b):
  `9fe140febe8747bbe2f06dfd570421331ec06f4b`; pack artifact filename is 2.8.10-beta.
- [Susy-Core v0.1.118](https://github.com/SymmetricDevs/Susy-Core/tree/2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a):
  `2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a`.
- [GroovyScript v1.4.3](https://github.com/CleanroomMC/GroovyScript/tree/6a3340814ce9527d23c2b88cdfd340b4f146b4c4):
  `6a3340814ce9527d23c2b88cdfd340b4f146b4c4`; its declared Groovy dependency is 4.0.30.

Tag resolution is verified; source-to-distributed-binary equivalence is not.
Do not mix newer Susy or GT source into this baseline merely because it exists.

## Pack index limitation

The tracked [index.toml](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/index.toml) is one newline. Its SHA-256
is `01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b`, while `pack.toml` declares
`257ef89f42af62d59016a704f07982a639c2e3cf6214aca69c371afd1ef777a9`.

This source snapshot is therefore **not a verified installable pack index**.
The specification uses immutable Git trees and explicit file/mod metadata.
Generating an index later creates a derived artifact whose relationship to the
source must be recorded; never silently repair or overwrite the observed source
baseline. The mismatch does not itself prove recipe defects.

The pack declares Minecraft 1.12.2 and Forge 14.23.5.2860; Cleanroom Relauncher is
an optional client dependency defaulting off. Those declarations do not establish
an applied Cleanroom runtime or exact transformed class composition.

## Selected dependency records

These are priority entry points, **not an exhaustive active-mod inventory**.
Artifact hashes and CurseForge coordinates are retained in the lock. No mod
binary was fetched or verified as part of this specification.

| Declared artifact | Pack authority | Status |
| --- | --- | --- |
| gregtech-1.12.2-2.8.10-beta.jar | [mods/gregtech-ce-unofficial.pw.toml](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/mods/gregtech-ce-unofficial.pw.toml) | Source tag resolved; binary equivalence open |
| Susy-Core-0.1.118.jar | [mods/susycore.pw.toml](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/mods/susycore.pw.toml) | Source tag resolved; binary equivalence open |
| groovyscript-1.4.3.jar | [mods/groovyscript.pw.toml](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/mods/groovyscript.pw.toml) | Source tag resolved; complete semantic closure and binary equivalence open |
| GregicalityMultiblocks-1.2.11.jar | [mods/gregicality-multiblocks.pw.toml](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/mods/gregicality-multiblocks.pw.toml) | Artifact metadata only; source closure open |
| gregtechfoodoption-1.12.2-1.12.10.jar | [mods/gregtech-food-option.pw.toml](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/mods/gregtech-food-option.pw.toml) | Artifact metadata only; source closure open |
| Supercritical-0.2.7.jar | [mods/supercritical.pw.toml](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/mods/supercritical.pw.toml) | Artifact metadata only; source closure open |
| SussyPatches-1.11.6.jar | [mods/sussypatches.pw.toml](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/mods/sussypatches.pw.toml) | Artifact metadata only; source closure open |
| !cleanroom-relauncher-1.1.3.jar | [mods/cleanroom-relauncher.pw.toml](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/mods/cleanroom-relauncher.pw.toml) | Artifact metadata only; source closure open |

The selected pack pins outrank Susy-Core's compile-time dependency examples when
identifying the intended pack composition. For example, pack GTFO, Supercritical
and SussyPatches records differ from Susy-Core's
[build dependency catalog](https://github.com/SymmetricDevs/Susy-Core/blob/2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a/gradle/deps.versions.toml).
Resolve source and effective injections from the pack-selected artifacts before
qualifying affected rules. Source repositories/commits for other dependencies
remain open, not guessed from version strings.

## Source entry points

The lock records repository, exact path, Git blob ID, SHA-256 and line count for
each reference. File contents/digests establish source identity, not complete
call-graph coverage. Read callers, inherited implementations and injected
modifications before turning an entry point into a qualified rule.

| Reference | Immutable source | Purpose |
| --- | --- | --- |
| `pack-manifest` | [pack.toml](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/pack.toml) | Pack label and declared Minecraft/Forge/index metadata |
| `pack-index` | [index.toml](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/index.toml) | Tracked index bytes; not a valid resolved pack inventory |
| `script-loaders` | [groovy/runConfig.json](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/runConfig.json) | Loader groups and directory order |
| `script-config` | [config/groovyscript.cfg](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/config/groovyscript.cfg) | GroovyScript construction policy |
| `map-mutation-api` | [groovy/preInit/MetaClassExpansions.groovy](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/preInit/MetaClassExpansions.groovy) | Dynamic RecipeMap metaclass setters |
| `map-definitions` | [groovy/prePostInit/Recipemaps.groovy](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/prePostInit/Recipemaps.groovy) | Pack aliases for effective recipe maps |
| `map-mutations` | [groovy/prePostInit/ModifyRecipeMaps.groovy](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/prePostInit/ModifyRecipeMaps.groovy) | Removal and effective map shape changes |
| `material-flags` | [groovy/classes/ChangeFlags.groovy](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/classes/ChangeFlags.groovy) | Material-generation flag helper |
| `coolant-recipes` | [groovy/postInit/chemistry/organic_chemistry/Coolants.groovy](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/postInit/chemistry/organic_chemistry/Coolants.groovy) | MIXER and BLENDER construction and capacity case |
| `circuit-recipes` | [groovy/postInit/chemistry/ChemistryOverhaul.groovy](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/postInit/chemistry/ChemistryOverhaul.groovy) | Circuit-indexed declarations |
| `helper-generated-recipes` | [groovy/globals/Photoresists.groovy](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/globals/Photoresists.groovy) | Helper loops and circuit/cleanroom-dependent construction |
| `distillation-source` | [groovy/postInit/chemistry/organic_chemistry/carboxyl_derivatives/AcidAnhydrides.groovy](https://github.com/SymmetricDevs/Supersymmetry/blob/3e83cd7bad57bd4c424de4e6cc707ab02fe32f54/groovy/postInit/chemistry/organic_chemistry/carboxyl_derivatives/AcidAnhydrides.groovy) | Ordered fluid outputs for generated distillery variants |
| `ingredient-matching` | [src/main/java/gregtech/api/recipes/Recipe.java](https://github.com/GregTechCEu/GregTech/blob/9fe140febe8747bbe2f06dfd570421331ec06f4b/src/main/java/gregtech/api/recipes/Recipe.java) | Ordered matching and consumption |
| `recipe-building` | [src/main/java/gregtech/api/recipes/RecipeBuilder.java](https://github.com/GregTechCEu/GregTech/blob/9fe140febe8747bbe2f06dfd570421331ec06f4b/src/main/java/gregtech/api/recipes/RecipeBuilder.java) | Builder validation, circuit declarations and transformations |
| `recipe-lookup` | [src/main/java/gregtech/api/recipes/RecipeMap.java](https://github.com/GregTechCEu/GregTech/blob/9fe140febe8747bbe2f06dfd570421331ec06f4b/src/main/java/gregtech/api/recipes/RecipeMap.java) | Map validation, compilation and selection |
| `circuit-ingredient` | [src/main/java/gregtech/api/recipes/ingredients/IntCircuitIngredient.java](https://github.com/GregTechCEu/GregTech/blob/9fe140febe8747bbe2f06dfd570421331ec06f4b/src/main/java/gregtech/api/recipes/ingredients/IntCircuitIngredient.java) | Circuit identity, amount and NBT behavior |
| `ghost-circuits` | [src/main/java/gregtech/api/capability/impl/GhostCircuitItemStackHandler.java](https://github.com/GregTechCEu/GregTech/blob/9fe140febe8747bbe2f06dfd570421331ec06f4b/src/main/java/gregtech/api/capability/impl/GhostCircuitItemStackHandler.java) | Absent and configured ghost states |
| `machine-registration` | [src/main/java/gregtech/common/metatileentities/MetaTileEntities.java](https://github.com/GregTechCEu/GregTech/blob/9fe140febe8747bbe2f06dfd570421331ec06f4b/src/main/java/gregtech/common/metatileentities/MetaTileEntities.java) | Registered machine factories |
| `machine-processing` | [src/main/java/gregtech/api/capability/impl/AbstractRecipeLogic.java](https://github.com/GregTechCEu/GregTech/blob/9fe140febe8747bbe2f06dfd570421331ec06f4b/src/main/java/gregtech/api/capability/impl/AbstractRecipeLogic.java) | Cache, eligibility, preparation and progress |
| `distillery-generation` | [src/main/java/gregtech/api/recipes/builders/UniversalDistillationRecipeBuilder.java](https://github.com/GregTechCEu/GregTech/blob/9fe140febe8747bbe2f06dfd570421331ec06f4b/src/main/java/gregtech/api/recipes/builders/UniversalDistillationRecipeBuilder.java) | Circuit-indexed derived recipes |
| `susy-map-callbacks` | [src/main/java/supersymmetry/api/recipes/SuSyRecipeMaps.java](https://github.com/SymmetricDevs/Susy-Core/blob/2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a/src/main/java/supersymmetry/api/recipes/SuSyRecipeMaps.java) | Map definitions and construction callbacks |
| `susy-machine-registration` | [src/main/java/supersymmetry/common/metatileentities/SuSyMetaTileEntities.java](https://github.com/SymmetricDevs/Susy-Core/blob/2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a/src/main/java/supersymmetry/common/metatileentities/SuSyMetaTileEntities.java) | Pack machine factories, including custom processors |
| `susy-context-rules` | [src/main/java/supersymmetry/common/util/RecipeCheckUtils.java](https://github.com/SymmetricDevs/Susy-Core/blob/2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a/src/main/java/supersymmetry/common/util/RecipeCheckUtils.java) | Atmosphere, biome and dimension consumers |
| `susy-catalysts` | [src/main/java/supersymmetry/api/capability/impl/CatalystRecipeLogic.java](https://github.com/SymmetricDevs/Susy-Core/blob/2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a/src/main/java/supersymmetry/api/capability/impl/CatalystRecipeLogic.java) | Catalyst selection, gates and overclock changes |
| `susy-ball-mill` | [src/main/java/supersymmetry/common/metatileentities/multi/electric/MetaTileEntityBallMill.java](https://github.com/SymmetricDevs/Susy-Core/blob/2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a/src/main/java/supersymmetry/common/metatileentities/multi/electric/MetaTileEntityBallMill.java) | Auxiliary occupied slots and effects before rejection |
| `susy-strand-processing` | [src/main/java/supersymmetry/common/metatileentities/multi/electric/strand/MetaTileEntityStrandShaper.java](https://github.com/SymmetricDevs/Susy-Core/blob/2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a/src/main/java/supersymmetry/common/metatileentities/multi/electric/strand/MetaTileEntityStrandShaper.java) | Custom non-RecipeMap processing |
| `susy-mixin-selection` | [src/main/java/supersymmetry/mixins/SuSyLateMixinLoader.java](https://github.com/SymmetricDevs/Susy-Core/blob/2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a/src/main/java/supersymmetry/mixins/SuSyLateMixinLoader.java) | Conditional dependency transformation selection |
| `susy-compile-dependencies` | [gradle/deps.versions.toml](https://github.com/SymmetricDevs/Susy-Core/blob/2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a/gradle/deps.versions.toml) | Build dependencies; not a substitute for pack pins |

## Refresh and extraction policy

1. Resolve the public pack default branch explicitly and obtain a separate clean
   source checkout. Do not pull over developer edits or mutate a canonical pack.
2. Record commit/tree and actual source bytes before inspecting recipe behavior.
   Parse the pack's own mod records; do not use unrelated current dependency heads.
3. Check declared hashes against available source/artifact bytes. Preserve
   discrepancies as structured findings. A filename/version label is not proof
   of binary provenance or active transformation state.
4. Follow Groovy loader configuration, helper/material sources, recipe producers,
   registration/mutation order, machine factories and active dependency hooks.
5. Update the lock and source references together, review semantic changes and
   invalidate affected construction data, queries and qualification.
6. Keep downloaded checkouts, binaries and generated state outside the installed
   module and outside public source tracking. Public files contain only appropriate
   product specifications, provenance references and later licensed implementation.

A candidate overlay remains separate from the immutable baseline and must have
its own content identity. Local checkout paths are not part of the portable
source lock. Reads may use caller-supplied cache roots, but must verify their
identity and reject stale or unexpectedly modified sources.

## Outstanding authority work

The source lock now includes the first implementation's referenced files.
`rules.json` maps implemented groups to hashed sources. `tools/axiom_sources.py`
verifies four explicitly supplied clean checkouts and the exact Coolants fixture.
Local checkout paths never enter the lock.
Qualification still needs source/artifact equivalence, full dependency and
producer/machine coverage, an explicit platform/configuration/mixin composition,
and the independently tested extraction. See
[conformance requirements](../tests/README.md).
