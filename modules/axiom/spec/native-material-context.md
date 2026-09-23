# Material producers in one native Cleanroom context

Status: internal, bounded producer qualification. This joins the retained material
algorithms to [actual Cleanroom constructor identities](native-identities.md).
It does not execute mod discovery, the complete pack lifecycle or recipe scripts,
and does not broaden the public recipe-validity contract.

## Ownership

The native material program is generated from existing retained Java sources,
not maintained as a second copy of the algorithms. Isolated-kernel dependency
types link to actual classes in the selected patched/remapped Cleanroom image.
One platform-parent class loader owns the program and those classes; the ambient
engine classpath is not a parent.

| State | Owner in this path |
| --- | --- |
| Fluids, stacks, delegates, defaults, IDs, registration events | Original native `Fluid`, `FluidStack`, `FluidRegistry`, `EventBus` |
| Material metadata, properties, builder queues, attributes, unification | Retained GT algorithms using those native fluid objects |
| Registry maps, integer identities, locations, clamp, dye values | Actual selected Minecraft classes; explicit SRG bindings |
| Active mod owner | Native `Loader`; qualification supplies explicit native container fixtures |
| Context lifetime, absent dependencies, tooltip/sprite records | Explicit Axiom host bindings; presentation stays excluded |

`GTFluid` extends original Forge `Fluid`. Material fluid getters return that actual
object; amount overloads construct original `FluidStack`. WATER/LAVA are never
converted, copied into another registry or registered again to populate a model.
References to `masterFluidReference` and `defaultFluidName` alias the original live
maps: GT's namespace correction mutates the same registry that dispatched events.

The patched `RegistryNamespaced` binary has raw inherited generic metadata that
prevents a generic Java subclass from compiling. Its host delegates to an actual
instance and aliases its maps instead. No map, ID allocator or iteration algorithm
is reimplemented for this binding.

## Source and execution boundary

The builder links 58 shared source files, ten host/event files and eleven
[native item/unifier source templates](native-items.md). Retained algorithms
keep their documented exclusions/substitutions from [material construction](material-construction.md),
[fluids](native-fluids.md), [registries](native-registries.md) and [prefixes](native-prefixes.md).
Six shared carriers/host ports and the host bindings are explicit shared
dependencies, not independent parity oracles. Generated sources are build output.

Qualification reconstructs algorithms from immutable upstream Git objects and
requires byte-identical compiled program classes. Complete GT
`ElementMaterials.register()` and Susy `SuSyUnknownCompositionMaterials.init()`
methods compile from locked, separately acquired source into temporary space.
No complete producer method is replaced with a handwritten declaration list.
This selected-producer fixture does not execute GT's aggregate registration
method. The separate [complete catalog qualification](material-catalog.md) does;
it stops before fluid registration and must not inherit this fixture's later
fluid-before-freeze ordering as the full lifecycle order.

The selected target remains Supersymmetry 0.1.16.15, GTCEu 2.8.10-beta, Susy-Core
0.1.118 and Cleanroom 0.6.12-alpha, on the profile-selected Temurin HotSpot
25.0.4+7 Linux x86_64 JDK. Existing target, construction, fluid, prefix and identity
locks supply exact commits/digests. The receipt also records original
Loader/controller/container sources used to audit the owner fixture.

The identity checkpoint has no `LoadController`. The test attaches an original
controller and original `DummyModContainer` owners for `gregtech`, `susy`, and
directed alternatives. This is **fixture-supplied ownership, not recovered GT/Susy
mod containers**. Material registration, fluid registration and dispatch read
the same native owner. A test listener observes actual native events; neither
fixture claims installed discovery or installed listener composition.

Each evaluation uses a fresh restricted JVM and external deadline. A context
admits only one material-program attempt, including failures. Programs are digest,
namespace and duplicate checked before construction. These are custody checks,
not an attestation for arbitrary caller-supplied code: this developer tool admits
the source-qualified program, not a public arbitrary-code endpoint.

## Required witnesses

- All 130 GT element and ten Susy unknown-composition declarations execute, then
  deferred fluid builders run after material registries close. Receipts retain
  material metadata, fluid scalar state, bindings, IDs, events and namespaces.
  GT material freeze is not full vanilla registry freeze.
- Listeners observe native GT ownership before retained GT Susy namespace
  correction; final native names reflect that correction.
- Explicit WATER/LAVA materials retain original fluid/block identity. Reusing
  water mutates that object without a new event.
- Alternative default selection rebinds existing and subsequent material stacks.
  The raw material fluid reference remains the original object. These observations
  intentionally differ: stacks follow delegates, not copied identity records.
- A throwing listener leaves pre-dispatch maps/delegates/attributes intact;
  post-dispatch sprites, owner correction, buckets and tooltips have not run.
  Retry reuses the partial registration without inventing another event or fix.
- Fresh English/Turkish runs agree. An original Aluminium color edit changes its
  captured material/fluid result without changing registration event order.
- Altered program bytes, namespace escape and duplicate classes reject.

## Offline build and qualification

Build the engine and qualify [native identity inputs](native-identities.md) first.
Then, from the repository root:

```sh
python3 tools/build_axiom_native_materials.py \
  --java-home PATH_TO_SELECTED_JDK --engine-home PATH_TO_INSTALLED_ENGINE \
  --images PATH_TO_QUALIFIED_NATIVE_IMAGES \
  --library-root PATH_TO_ORIGINAL_LIBRARIES --output NEW_PROGRAM_DIRECTORY

python3 tools/axiom_native_material_conformance.py \
  --java-home PATH_TO_SELECTED_JDK --engine-home PATH_TO_INSTALLED_ENGINE \
  --images PATH_TO_QUALIFIED_NATIVE_IMAGES \
  --library-root PATH_TO_ORIGINAL_LIBRARIES --program PROGRAM_DIRECTORY \
  --gtceu PATH_TO_GTCEU --susy-core PATH_TO_SUSY_CORE \
  --cleanroom PATH_TO_CLEANROOM --report NEW_RECEIPT.json
```

Both commands are offline and refuse output overwrite. Build success is not
producer qualification: `program.json` records `producerExecutionQualified: false`.
A separate receipt binds the exact program, engine, sources, native inputs and
results. Neither marks installed composition or whole-pack parity qualified.

The engine embeds host/event and native item/unifier source templates, not generated programs, Minecraft
images or Susy producer binaries. Generated material sources retain upstream
notices. Acquired producer sources/classes remain temporary qualification inputs;
see [notices](../sources/NOTICE.md).

## Next vertical

The [complete GT catalog](material-catalog.md) now binds native configuration and
live catalog dependencies through the frozen material checkpoint. The
[native item stage](native-items.md) joins vanilla items and ore membership. Next, recover
generated content and extend complete Susy/addon producers and actual lifecycle ownership. Unexecuted
fields must not become fabricated registry entries. Addons are explicitly excluded
here (`topAddonsLoaded` is false); missing full prefix configuration fails closed.
Native fluid blocks, generated GT item/ore membership, full transformations/listeners
and Groovy recipe registration remain separate dependency gates. Isolated kernels
remain comparison fixtures, not another fluid authority for this path.
