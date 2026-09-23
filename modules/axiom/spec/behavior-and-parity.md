# Machine behavior and parity

Status: target specification. Ordinary MIXER start is implemented within the
[first-slice boundaries](first-slice.md); whole-pack parity is not qualified.

## Scope and exactness contract

Target every registered recipe-processing family in the selected Supersymmetry
composition, including custom processors that bypass RecipeMap.

For the same admitted source/dependency composition, initial state, inputs,
configuration, ordered external events and random choices, Axiom must produce
the same covered processing decisions and state changes as the game code.

This includes surprising behavior and upstream defects. Developer-intent
diagnostics may flag them, but must not change the engine's reported semantics.
A source version label or large test count is not a universal parity proof.

The scope includes registration, loading inputs, selecting recipes, eligibility,
preparation, consumption, progress, interruption and completion. It excludes
visuals and unrelated game subsystems. Recipe-relevant environment interactions
must be modeled explicitly or reported unsupported.

## Preserve distinct questions

1. **Definition validity:** does the actual construction/registration path accept
   the effective recipe, and does it remain reachable?
2. **Machine compatibility:** can a legal supported machine configuration process
   it? A positive existential answer needs a concrete witness that the actual
   evaluator accepts. Failure to find a witness is not universal impossibility.
3. **State-specific behavior:** what does this exact machine state select and do?
4. **Time-dependent behavior:** what happens under this declared event sequence?
5. **Output recovery:** which products are retained, discarded or otherwise routed?

Each answer states its context and coverage. A source-only result may identify
required conditions without claiming they hold in a particular world.

## Machine catalog

Resolve registered IDs through factories, inherited and anonymous workable
classes, active map/mode, handler constructors, applicable mixins and reachable
dependencies. Include source/bytecode transformation identity before qualifying
an installed composition. A declared class or map is not proof of activation.

Record local slot/tank roles, handler identity/order, ghost circuits, bus scope,
capacities, admission routes, output locks, auxiliary providers, cache fields,
property consumers, transformation hooks and ordered effects.

Start at [GT machine registration](https://github.com/GregTechCEu/GregTech/blob/9fe140febe8747bbe2f06dfd570421331ec06f4b/src/main/java/gregtech/common/metatileentities/MetaTileEntities.java) and
[Susy machine registration](https://github.com/SymmetricDevs/Susy-Core/blob/2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a/src/main/java/supersymmetry/common/metatileentities/SuSyMetaTileEntities.java).
[Susy mixin selection](https://github.com/SymmetricDevs/Susy-Core/blob/2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a/src/main/java/supersymmetry/mixins/SuSyLateMixinLoader.java) contributes
conditional activation. Every registered processor eventually needs an explicit
coverage disposition, not an implicit generic fallback.

## Shared semantics that must remain literal

- Slot insertion acceptance is separate from recipe matching. Preserve actual
  GUI versus capability routes and concrete handler restrictions.
- [Recipe.java](https://github.com/GregTechCEu/GregTech/blob/9fe140febe8747bbe2f06dfd570421331ec06f4b/src/main/java/gregtech/api/recipes/Recipe.java#L192-L358) uses ordered
  greedy allocation, not a backtracking solver. Keep split quantities, unused
  inputs, nonconsumable behavior and empty-category preconditions.
- Preserve typed NBT, registry identity, ore expansion/phase, wildcard
  construction and capability compatibility. Names/counts alone are insufficient.
- [RecipeMap lookup](https://github.com/GregTechCEu/GregTech/blob/9fe140febe8747bbe2f06dfd570421331ec06f4b/src/main/java/gregtech/api/recipes/RecipeMap.java#L468-L691) and
  [cached machine selection](https://github.com/GregTechCEu/GregTech/blob/9fe140febe8747bbe2f06dfd570421331ec06f4b/src/main/java/gregtech/api/capability/impl/AbstractRecipeLogic.java#L374-L405)
  are distinct operations. Keep tree construction/traversal, original ordering,
  cache state, distinct buses and custom lookup behavior.
- Preserve Java overflow, integer division, truncation, floating-point operations,
  rounding and short-circuit behavior. Do not silently clamp or normalize values.
- Recipe properties require actual consumer tracing. Presence does not prove
  every machine using a map enforces the property.
- Preparation must retain output trimming, overclocking, parallel transformations,
  auxiliary resources, energy buffer checks and concrete output/void behavior.

## Circuits and modifiers

[IntCircuitIngredient](https://github.com/GregTechCEu/GregTech/blob/9fe140febe8747bbe2f06dfd570421331ec06f4b/src/main/java/gregtech/api/recipes/ingredients/IntCircuitIngredient.java) is a nonconsumable
selector in the normal builder path; `circuitMeta` does not itself multiply
duration, EU/t or outputs. [Ghost circuits](https://github.com/GregTechCEu/GregTech/blob/9fe140febe8747bbe2f06dfd570421331ec06f4b/src/main/java/gregtech/api/capability/impl/GhostCircuitItemStackHandler.java)
distinguishes absent (-1) from configuration zero and configurations through 32.

Represent physical and ghost circuits independently and preserve per-bus
visibility. Circuit ingredient entries count toward map limits even where the
machine adds a ghost slot. Duplicate declaration, normalization, full-NBT lookup
and matching must follow their different source operations.

Separately preserve construction-time circuit-indexed variants, active map
selection, catalyst effects and preparation-time transformations. Do not model
them all as a single circuit integer or generic modifier function.

## Specialized behavior and effects

[Susy context checks](https://github.com/SymmetricDevs/Susy-Core/blob/2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a/src/main/java/supersymmetry/common/util/RecipeCheckUtils.java) include positional
atmosphere, biome and dimension reads.
[Catalyst logic](https://github.com/SymmetricDevs/Susy-Core/blob/2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a/src/main/java/supersymmetry/api/capability/impl/CatalystRecipeLogic.java) uses inventory-dependent
state and overclock hooks. [Ball-mill logic](https://github.com/SymmetricDevs/Susy-Core/blob/2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a/src/main/java/supersymmetry/common/metatileentities/multi/electric/MetaTileEntityBallMill.java#L332-L397)
requires qualifying occupied slots and can apply damage before superclass
preparation subsequently rejects.

[Strand processing](https://github.com/SymmetricDevs/Susy-Core/blob/2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a/src/main/java/supersymmetry/common/metatileentities/multi/electric/strand/MetaTileEntityStrandShaper.java#L95-L148)
does not use the ordinary recipe-map pipeline. It must not disappear from the
catalog because its methods have different names.

Queries evaluate a copy of domain state or return ordered transitions. Preserve
effects preceding failure, cache/normalization changes and actual output
discarding policy. Do not call an impure check repeatedly and assume equivalence.

## Time, environment and uncertainty

Logical processing steps take explicit environment facts/events. Preserve reads
at the right time and position, provider state, interruption/recovery policy and
random-state consumption. For unsupplied randomness, return possible outcomes or
a declared missing input, not fabricated deterministic results.

A formation evaluator can derive structure properties; otherwise supplied
structure values remain assumptions. The engine must never use a favorable
fake-world/null-world default to certify environmental eligibility.

Acceptance is scoped to complete, qualified rules and supplied context.
Missing context, unsupported code, incomplete construction and exceeded bounds
remain distinct from an upstream rejection. An evaluation can report an observed
rejection while separately disclosing unexamined/unsupported stages.
