# Native RecipeMap extensions

Axiom's saved-workspace material command can execute Supersymmetry's complete
`preInit/MetaClassExpansions.groovy` against real GTCEu RecipeMap objects. This is
a bounded language and constructor capability, not recipe or machine validity.

## Native behavior

The selected pack installs `modifyMaxInputs`, `modifyMaxOutputs`,
`modifyMaxFluidInputs`, and `modifyMaxFluidOutputs` using Groovy metaclasses.
Each closure captures a different field name, writes that private field through
native `delegate.@...` access, and returns its actual receiver for chaining.
The original source runs without rewriting or checksum-based bypasses.

These methods are intentionally different from GT's public setters:

- Public setters respect the constructor's modification flags and take the
  maximum of the existing and requested value. They cannot decrease a limit.
- The pack's extensions directly assign the limit, bypassing those flags and
  permitting decreases. Groovy retains numeric coercion and native exceptions.

Axiom does not translate one route into the other, infer a recipe's validity from
the limits, or impose replacement recipe rules.

## Construction and native inputs

The complete original RecipeMap, RecipeBuilder and SimpleRecipeBuilder class
bytes come from the mod artifacts selected by the pinned pack metadata. The
constructor runs normally: it connects the builder, creates category metadata,
registers the map, and creates its native Groovy virtualized registry when GT's
Groovy module is enabled. Axiom does not fabricate a map or allocate it without
its constructor.

`build_axiom_material_api.py` requires `--supersymmetry` (selected source checkout)
and `--pack-mods` (explicit local artifact directory), alongside the existing
native inputs. The build verifies the selected pack's hashes, records SHA-256
and provenance for every retained class, and excludes unrelated shaded packages.
GT, GroovyScript, CodeChickenLib, ModularUI and the selected
[pack material addon inputs](pack-material-context.md) are local runtime
dependencies, not bundled in the independently distributed Axiom engine.

Existing source-bounded material/language owners have explicit precedence over
binary owners. The API manifest records its source overrides. RecipeMap and its
builders cannot be overridden by those generated sources. Class availability is
not permission to execute every API in an input artifact.

The standalone context supplies native module/container state explicitly. GT's
real ModuleManager selects `gregtech:grs_integration`; the real GroovyScriptModule,
ExternalModContainer and PropertyContainer receive the constructor's registration.
This is not execution of FML discovery, full `onCompatLoaded`, or the pack's addon
listeners. Object mapper integration and other lifecycle work remain incomplete.
Original Cleanroom annotation parsing and ModAPITransformer remove absent optional
API signatures; CraftTweaker is not fabricated to satisfy linkage. CodeChickenLib
shares the native loader's Minecraft identities, not a parallel parent-loader copy.

## Evidence and feedback

`execution.recipeMaps` contains actual limits, modification flags, hidden state,
and builder/category/virtualized-registry identity checks collected after execution.
The order of both four-element arrays is item inputs, item outputs, fluid inputs,
fluid outputs. Only maps reached by candidate execution are observed; an unused
registry is not initialized just to fill the report. Baseline/candidate comparisons
include this section, using separate fresh native workers.

The existing IDE/CLI saved-workspace command supplies native diagnostics and
navigation into the captured source. Removing the extension file removes its
methods in the next fresh worker; no legacy adapter reinstalls them. A native
public-setter failure is retained as a source error, while unresolved JVM linkage
is an incomplete context with its original diagnostic preserved.

`axiom_material_observation_conformance.py --authoring-only --recipe-map-authoring
--supersymmetry ...` compares complete programs under the selected original
GroovyScript classloader and installed operation. It checks four-field capture,
increases/decreases, flag enforcement/bypass, receiver identity, separate map
instances, coercions, errors and extension removal. The reference and installed
worker share selected native dependencies and explicit context preparation; this
is not an independent whole-pack oracle or evidence of universal parity.

## Remaining boundary

No `buildAndRegister`, recipe removal, ingredient/object-mapper resolution,
Susy-Core recipe lifecycle, circuit behavior or machine-slot validity is qualified
by this slice. Availability of RecipeMaps classes does not mean its populated
registry or Susy-Core mixins have run. The next work is actual pack material
dependency/composition closure, followed by native recipe edits and removals.
Clean results remain unqualified; the selected Java 25 toolchain remains provisional,
not evidence of the exact pack-installed JRE. Minecraft is not launched.
