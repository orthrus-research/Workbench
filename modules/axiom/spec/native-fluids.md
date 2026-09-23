# Native fluid construction and isolated producer execution

Status: implemented internal prerequisite, not a public native recipe endpoint.
The selected JVM can execute the retained material-builder → property/queue →
fluid-builder → registration → lookup chain. The complete original Susy-Core
`SuSyUnknownCompositionMaterials.init()` is exercised from its pinned source,
including Coolant and AdvancedCoolant. This does **not** execute
`Coolants.groovy`, initialize vanilla, dispatch pack events, or qualify a whole
installed Supersymmetry composition.

## Exact authority and ownership

`sources/native-fluids.lock.json` binds original Git blobs and SHA-256 values
at these immutable selected revisions:

- GTCEu: `9fe140febe8747bbe2f06dfd570421331ec06f4b`.
- Cleanroom: `fe78db8dc4fcee47df7549230858f4c064208d73` (0.6.12-alpha).
- Susy-Core: `2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a` (0.1.118).

The associated pack remains Supersymmetry 0.1.16.15, commit
`3e83cd7bad57bd4c424de4e6cc707ab02fe32f54`. These pins are not a claim that
upstream HEAD was refreshed. The comparison reads immutable Git objects, so a
different checkout HEAD cannot accidentally supply newer Cleanroom methods.

Cleanroom owns the JDK and original utility-library identities. Axiom owns
semantics and the standalone engine; Core continues to own provisioning,
process lifetime, cancellation and cleanup. No game launcher, fake world,
Workbench game harness, old namespace adapters or modeled JVM is introduced.

| Domain | Retained authority | Explicit boundary |
| --- | --- | --- |
| Material construction | Full Material.Builder, MaterialInfo verification, material verification/registration, flags and properties | See the expanded construction domain; actual prefix/enchantment/addon dependencies remain unqualified |
| Fluid construction | Complete FluidBuilder decision path except unavailable external branches; GTFluid/attributes, storage keys, FluidProperty and queue | World blocks, localized presentation and addon composition are not supplied |
| Fluid identity | Forge scalar Fluid methods and registration/name/bucket methods; GT ownership repair and FluidUnifier | Empty isolated registry, not vanilla's WATER/LAVA bootstrap or world default reload |
| Native utilities | Original Minecraft ResourceLocation and named registries, original selected Guava BiMap, selected Fastutil | Untransformed utility bytecode; installed transformation equivalence remains unqualified |

## Extraction contract

`tools/axiom_fluid_sources.py` selects complete source members with
comment/string-aware brace matching. Exact extraction comparison rejects drift.
Package, annotations and type names are relocated; Java string literals,
control flow, arithmetic, setter order and failure text are preserved.

`FluidMaterial` extends the existing material carrier but owns the actual
builder-created property object and source material metadata. The superclass and
native material share that one object. The builder now includes composition,
elements, average color, polymer and the other native property operations;
see [material construction](material-construction.md) for exact scope and
unresolved dependencies. Those dependencies are not populated with placeholders.

`FluidDomain` activates FLUID through the source's existing
`MaterialProperties.addBaseType` extension point, leaving the earlier
property-only extraction independently comparable. BLAST is a distinct real
property key. Native flags, icon sets and the base-type catalog retain static
state: a production evaluation must use a fresh disposable worker, not reuse a
JVM between unrelated targets. Fluid keys, registries, tooltips, unification,
sprites and registration observations belong to an explicit thread-confined
`FluidEnvironment`; nested environments are rejected.

The original ResourceLocation class (`nf`) executes through the same
hash-verifying platform-parent classloader as the native registries. No
Minecraft class or JAR is redistributed. Guava HashBiMap executes from the
profile-selected original library input, not a hand-written replacement.

## Registration is not transactional

The source order is:

1. Infer/store builder name, textures and state; resolve a primary or alternative
   existing fluid, otherwise construct the native fluid object.
2. Apply attributes and scalar fields in original order: temperature, color
   if enabled, density, luminosity and viscosity.
3. Register a new fluid, or add a requested bucket for an existing fluid.
   Forge inserts the master reference and delegate **before** duplicate-name
   detection. A fresh registration inserts names/IDs/defaults, then posts its
   event. GT's sprite collection and ownership repair occur **after** that event.
4. Associate the material through FluidUnifier and append a lazy tooltip binding.
5. Handle an optional world block, then TOPAddons color integration.
6. Return the fluid; only then does the queue store its material association.

A later failure keeps earlier effects. A failed event leaves registry entries
without GT ownership repair/buckets. An unavailable block can leave a fluid
registered, bucketed and unified while its material queue remains unfinished.
Retrying such a queue does not mean starting from an empty registry.

Source-specific behavior preserved by tests includes:

- Ordinary material liquid defaults: 293 K, density/viscosity 1000, luminosity
  **-1**, not a corrected/clamped zero. Dust liquid defaults to 1200 K and
  luminosity 10. Explicit Latex temperature stays 293 despite its dust property.
- GLOWING and STICKY flags affect luminosity and viscosity. Plasma is queued
  after higher-priority fluids and can add the primary fluid temperature to
  10000 K. Blast-derived integer arithmetic retains JVM overflow.
- Builder reuse retains inferred fields; alternative-name reuse mutates an
  existing fluid without replacing its original state flag or owner.
- Attributes are identity-keyed and insertion-ordered. Foreign fluids that
  cannot accept them produce a warning rather than an invented rejection.
- White/custom textures can disable color updates. Density/viscosity double
  conversion keeps JVM NaN, infinity, signed-zero and truncation behavior.
- Forge's bucket-set snapshot is cached and is not invalidated by later
  additions. FluidUnifier keys by fluid name, not object identity.

## Honest external boundaries

`isolatedProducer` explicitly chooses an empty registry with no listeners or
addons. It is suitable for testing a closed source producer, never a substitute
for the pack's missing registration universe.

`unresolvedPack` throws `incomplete` at event dispatch (or unresolved addon
composition when no new registration event occurs). It does not silently
assume there are no listeners. Directed callback probes test event *position*,
not Forge's actual event dispatcher.

Texture identifiers currently use the source's server-side path projection.
Client resource-pack fallback/cache behavior is not executed. Tooltip records
retain object/material/state references and insertion order but do not execute
localized suppliers. Requesting world-block construction returns `unsupported`
at its original position. [Native fluid stacks and default rebinding](native-fluid-stacks.md)
now retain the in-memory default/delegate lifecycle and NBT default-list methods.
World/file/network reload, actual vanilla fluid identities and container capabilities
remain unqualified. No API advertises those excluded operations as covered.

## Qualification

Build and directed tests:

```sh
python3 tools/build_axiom.py --provision --registry-root PATH_TO_ORIGINAL_LIBRARIES
```

Source comparison, using explicit locally acquired upstream Git repositories:

```sh
python3 tools/axiom_fluid_conformance.py \
  --gtceu PATH_TO_GTCEU --susy-core PATH_TO_SUSY_CORE --cleanroom PATH_TO_CLEANROOM \
  --java-home PATH_TO_SELECTED_JDK \
  --engine-home modules/axiom/jvm/build/install/workbench-axiom-engine \
  --registry-root PATH_TO_ORIGINAL_LIBRARIES --report NEW_RECEIPT.json
```

The CI lane can fetch only the pinned Cleanroom commit using
`--provision-cleanroom NEW_DIRECTORY`. Inputs, extraction recipe, sources JAR,
engine JARs, original utility policy, selected JDK and shared dependency sources
are bound in the no-clobber receipt and rechecked after execution.

The comparison independently compiles the locked FluidBuilder and selected
Forge Fluid members, comparing 4096 builder and 4096 scalar scenarios. It also
compiles the **entire** locked Susy initializer plus its original `susyId`
helper, with the namespace bound to the original Gradle declaration, then
constructs and resolves all ten declared materials/fluids. That acceptance test
uses the retained dependency domain; it is not a second independent oracle
for every material/registry dependency. Shared ports and their common-mode
limitations are explicit in the receipt.

Susy-Core producer source/classes are temporary test inputs, not bundled GPL
implementation. The Forge excerpts retain their original LGPL-2.1 headers and
license; GT excerpts retain LGPL-3.0 provenance. Sources ship in the engine's
sources JAR. No public publishing operation is performed by these tools.

## Next dependency gate

The [native material context](native-material-context.md) now connects the retained
builders to actual Cleanroom WATER/LAVA, FluidRegistry, stacks, delegates and event
dispatch for complete GT element and Susy unknown-composition producers. Its native
owner/listener fixtures are explicit, not installed mod discovery. The isolated
kernel described above remains separately qualified for comparison.

Close **native phased material bootstrap**: actual listener discovery/dispatch,
GT/Susy/pack producer ordering, later material/property modifications, and
generated item/ore identities needed by the Coolants recipe chain. Bind the
vanilla/default fluid baseline and selected client/addon context explicitly.
Then execute the actual GroovyScript recipe-producing path and builder
callbacks against those constructed identities. Do not promote this isolated
producer test to a recipe-validity result or add more modeled VM behavior.
