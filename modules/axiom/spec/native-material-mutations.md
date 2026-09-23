# Native material mutations

The pack context executes the selected pack's complete `ChangeFlags.init()`
through original Groovy dispatch and GTCEu/Susy implementations. It does not
translate material declarations into a second rules engine. The selected source
is Supersymmetry 0.1.16.15, GTCEu 2.8.10-beta and Susy-Core 0.1.118; immutable
revisions and source hashes are in [the source inventory](../sources/material-program.lock.json).
These are selected authorities, not a claim about upstream latest.

## Catalog and configuration dependency closure

The original `RecipeMaps` and `SuSyRecipeMaps` class initializers run intact.
This includes recipe builders, categories, virtualized registries, UI metadata
and callback registration. Retaining UI metadata does not launch rendering.
Susy's initializer installs callbacks on GT maps, including the mixer callback;
copying just the blast, pyrolyse and railroad fields would lose those effects.
Observing a registered callback is not executing or qualifying its recipe logic.

The old reduced `GTValues`, `ConfigHolder` and `BlastProperty` owners have been
removed from Axiom's generated API. Their complete selected binary owners and
nested classes are retained. This restores native date-sensitive suppliers,
configuration defaults and Groovy blast-gas parsing. Build checks prohibit
overriding these owners or native recipe classes with reduced implementations.

Before catalog initialization, Cleanroom's original `ASMModParser` registers
the original GT `ConfigHolder` annotations with `ConfigManager.loadData`, followed
by original `ConfigManager.sync`. The loader's config path points to the disposable
saved-input copy. No configuration values are assigned or parsed by an Axiom
replacement. `execution.gregtechConfiguration` records the input and worker-file
hashes, absent-input/default status and selected values. Native config saving
affects only the disposable copy, never the developer's saved source.

The selected original `RecipeMapsMixin` is loaded with its original refmap before
the target catalog. Selection excludes unrelated client and common mixins and
is explicitly not the complete installed mixin set. Passive transformation
observations confirm the native mixin merge. Its later recycling behavior is
not exercised by material initialization.

## Mutation families and actual behavior

Exact native descriptors admit dust/ingot/ore/blast mutations, fluid pipes,
queued fluid types and slurry/acid/base attributes, ore byproducts and direct
smelt state, wire amperage, moderator builders and mill balls. Existing flags,
formula/color, tool, fiber, DummyABS and fission paths remain native. The policy
admits families, not particular pack material names. GT-base admission is not
expanded by the pack profile.

Some behavior is easy to misrepresent if inferred from method names:

| Source operation | Selected original behavior |
| --- | --- |
| Reduce railroad recipe-map input limits to 12 items / 3 fluids | Setters use `Math.max`; limits remain 16 / 4. |
| Add a new blast property with duration/EU arguments 480 / 240 | The original expansion forwards them to the builder in reversed semantic order: observed EU 480, duration 240. |
| Modify an existing blast property with the same arguments | Original setters yield EU 240, duration 480. |
| Invalid blast gas tier while Groovy scripts are running | Original parser logs an error and returns null; not an Axiom exception. |
| Missing fluid property/storage key or ore property in Susy expansions | Original code logs and returns. |
| Nonpositive queued fluid temperature or invalid dust harvest setter | Original implementation throws, retaining the saved source location. |
| Negative moderator temperature or mill-ball durability | These original constructors do not reject the value. Axiom must not invent a rejection or a validity guarantee. |

Passive observations expose attached native property values, original ordered ore
byproducts and queued FluidBuilder temperatures/attributes. Observation does not
register fluids, drain queues, create generated content or invoke callbacks.

## Completion evidence and limits

The unchanged saved pack returns from all material producer groups and
`ChangeFlags.init()`. Completion requires both the outer caller's **Finished
modifying material flags** log and tail-state witnesses: fission/moderator
properties and the steel/stainless mill-ball values. The internal log at
ChangeFlags line 626 is insufficient: nuclear and mill-ball edits follow it.

The observed default registry contains 3,441 materials. The subsequent
[custom-item increment](native-custom-items.md) also completes PostMaterialEvent:
409 native variants are declared and the material registry reaches FROZEN.
Pack-wide generated content remains an Axiom composition gap, not a pack error.
It does not qualify complete addon discovery/order, generated content, fluid or
recipe registration, machine behavior, or exact installed-pack JRE parity.

`tools/axiom_pack_mutation_conformance.py` runs 28 complete saved fixture programs
and optionally the unchanged full pack in fresh workers. It tests native state,
logged/thrown errors, catalog identity/mixin linkage and isolation. The installed
command lane additionally exercises an error without an observation request,
developer correction and material addition/removal in a disposable complete pack
copy. These lanes share native dependencies with production; they are regression
evidence, not an independent oracle for whole-pack parity.

## Timing and next vertical

`result.workerStages` separates runtime verification, source intake/admission,
native bootstrap and material-context execution. Nested
`execution.initialization` retains compilation, configuration and native event
durations. These are explicit host boundaries, not per-listener attribution or
the entire CLI duration; JVM launch, environment resolution and transport also
contribute. Returned stages may contain native errors and incomplete coverage.

Resource and timing targets are suspended until post-MVP optimization; completed
native scope and correct diagnostics remain the acceptance criteria.

Custom declaration and bounded GT generated-content coexistence are described in
[native custom items](native-custom-items.md). Next: compose remaining addon
registry prerequisites and pack-wide generated content. Do not skip scripts,
synthesize items or reuse mutable registries to obtain a passing baseline.
