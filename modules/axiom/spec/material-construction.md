# Native material construction

This is an internal bootstrap dependency, **not completion of the Coolants
registry vertical**. It constructs native material objects; it does not yet
recover the complete GT/Susy/pack material universe, generated meta-items,
ordered `dyeCyan` membership, or a recipe-validity verdict.

The [native material context](native-material-context.md) now links this retained
construction code directly to native Cleanroom fluids, stacks, registries and
owner/event dispatch. This page still documents the separately qualified isolated
kernel; its fixture registry is not copied into the native context.

## Source authority

`sources/material-construction.lock.json` records immutable Git blobs and
SHA-256 identities at GTCEu
`9fe140febe8747bbe2f06dfd570421331ec06f4b`. The extraction recipe is
`tools/axiom_construction_sources.py`, together with the existing fluid/property
recipes. These are selected inputs, not a refresh of upstream HEAD.

The implementation retains the full native `Material.Builder` and
`MaterialInfo`, construction-relevant Material instance methods, Element/Elements,
MaterialStack/SmallDigits, the complete GT property-key and flag catalogs,
wood/polymer/ore/tool/rotor/wire/pipe property bodies, EnchantmentLevel,
MarkerMaterial and its interning registry. The existing fluid/property and native
registry implementations provide the other dependencies. Markers remain outside
the material registry and retain the source's unset property owner/component list.

The common MaterialState superclass owns one property collection. Material
verification and flag checks operate on that same object. No second property
store, compatibility namespace or legacy material hierarchy is maintained.

## Behaviors that must not be cleaned up

- `components(Object...)` appends cast Material/Integer pairs. A malformed later
  pair leaves preceding additions intact. The MaterialStack-varargs overload
  replaces the list with `Arrays.asList`; adding to it later can throw. The
  ImmutableList overload retains its original immutability semantics.
- `build()` copies components using the selected Guava ImmutableList, verifies
  metadata, constructs/verifies/registers the material, then executes deferred
  postprocessors. Failures are not transactions or automatic rollback.
- Chemical formulas retain nesting and numeric subscripts. A one-component,
  amount-one material reuses its component formula. Isotope hyphens are kept;
  `HELIUM_3` is not an alias for the registered `Helium-3` element name.
- Average color is an arithmetic average of **packed color integers**, using
  the original long accumulator and int divisor. It is not channel-wise color
  blending. Zero totals, overflow, and integer division retain JVM behavior.
- Ingot/gem builders override an existing dust harvest level only when it equals
  two, and burn time only when zero. Constructors and setters have different
  validation. Polymer verification adds dust, ingot and its three native flags.
- Flag dependencies add other flags; missing required properties produce
  warnings, not fabricated properties or an invented rejection.
- Ore verification can change other materials. A later missing fluid queue or
  invalid property retains those earlier changes. Byproduct index clamping runs
  original selected Minecraft MathHelper bytecode, not a replacement algorithm.
- Pipe conflicts, wire narrowing, IV foil generation, rotor setter checks and
  enchantment-level flooring/capping are original property behavior. The rotor
  property has no default constructor; native default construction can return null.

## Explicit unresolved dependencies

The [native prefix catalog](native-prefixes.md) now executes the source's four
wood-fluid-pipe prefix-ignore calls when live material-catalog/configuration
dependencies are bound. Without those inputs, the call stops with
`incomplete / material.ore-prefix`, retaining previous effects. The isolated
element/construction probe deliberately leaves those inputs unbound; it does not
claim full GT registration or generated membership.

Tool properties have a typed `EnchantmentIdentity` dependency port. The
[native identity checkpoint](native-identities.md) now binds actual Cleanroom-patched
enchantments and executes five original GT tool-stat expressions with them. The
isolated construction comparison still uses labelled fixture identities (or null,
which the original map accepts). Neither witness proves mod-provided enchantment
membership or complete material-producer execution.

The next dependency checkpoint retains [native Forge registry construction and
state](native-forge-registries.md): entry/delegate identities, ID allocation,
overrides and wrapper factories. That generic machinery is exercised with custom
fixtures only. Actual GameData initialization, vanilla entries and WATER/LAVA
construction are separately qualified by the native identity checkpoint, not
manufactured in these isolated registry fixtures.

[Native fluid stacks and default rebinding](native-fluid-stacks.md) now support
the original material amount/plasma overloads and solidifying-fluid stack creation.
These require actual registered fluid delegates. NBT uses separately supplied
original utility bytecode; no WATER/LAVA or enchantment identity is fabricated.

Fluid pipe properties retain construction/state methods, not the unimplemented
inherited stack filter or client tooltip interface. Acid attribute identity is
owned by the fluid environment; its client-localization suppliers remain lazy
and fail explicitly when requested. Existing fluid assignment preserves the
provided fluid identity. The native checkpoint creates actual WATER/LAVA in its
external class space. The [native material context](native-material-context.md)
now links retained material producers to that single registry, with native
delegate/event behavior and explicitly supplied owner fixtures. It does not
copy those fluids into the isolated comparison registry or qualify complete
installed mod ownership.

The selected Guava 33.6.0-jre and Commons Lang 3.20.0 execute immutable-list and
pair behavior directly. Both artifact hashes are checked against the Cleanroom
library policy in qualification. Original Minecraft utility inputs are provided
separately; no Minecraft classes or mod binaries are distributed with Axiom.

Static native catalogs require the existing fresh, isolated worker per
evaluation. Creating another FluidEnvironment in the same JVM is **not** an
independent source-evaluation class space.

## Qualification and producer boundary

```sh
python3 tools/axiom_construction_conformance.py \
  --gtceu PATH_TO_GTCEU --java-home PATH_TO_SELECTED_JDK \
  --engine-home modules/axiom/jvm/build/install/workbench-axiom-engine \
  --registry-root PATH_TO_ORIGINAL_LIBRARIES --report NEW_RECEIPT.json
```

The comparison compiles original construction sources separately, runs both
implementations in fresh selected JVMs under worker kernel restrictions, and
compares 4,096 state/failure vectors. Shared dependency ports are explicitly
recorded, so matching results are not an independent proof of those ports.

The producer witness compiles and executes **all of the original
ElementMaterials.register method** (130 declarations), not selected elements. The
original Materials static fields and initializer are retained, including native
marker creation and flag presets. Its aggregate `Materials.register()` method
is deliberately not compiled or called: later material groups, full marker color
initialization and OrePrefix.init remain separate dependencies.

Every resulting element material contributes identity, composition, formula,
color, mass, properties and flags to the comparison digest. An independently
compiled source edit changes Aluminium's color by one and must change that
producer digest while leaving unrelated fixture-vector results unchanged.
The edited source gets its own identity; it is not accepted as the locked source.

These observations do not establish generated dust identities, fluid-generation
closure, addon/configuration/mixin equivalence, or complete ordered ore membership.
The public `target` operation stays non-executing and no `bootstrap` endpoint is
admitted by this implementation.
