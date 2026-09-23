# Native construction: dependency gate

Status: **the real Coolants construction chain is not implemented**. Native
builder-field validation, an internal native material-event lifecycle, a dust/gem/ingot material-property kernel,
native material registries, native fluid construction in an isolated producer
universe, a fluid registration queue and worker kernel isolation are implemented; none
substitutes for the pack's registry/material bootstrap. No arbitrary-source
execution endpoint or new pack-validity capability has been registered.

This gate uses Supersymmetry 0.1.16.15 at
`3e83cd7bad57bd4c424de4e6cc707ab02fe32f54`, GTCEu at
`9fe140febe8747bbe2f06dfd570421331ec06f4b`, Susy-Core at
`2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a`, and GroovyScript at
`6a3340814ce9527d23c2b88cdfd340b4f146b4c4`. These are immutable selected
inputs, not a claim to have refreshed today's upstream heads.

## Why the file is not a closed program

The original `groovy/postInit/chemistry/organic_chemistry/Coolants.groovy`
imports `prePostInit.Recipemaps.*` and `gregtech.api.GTValues.*` and registers
MIXER and BLENDER recipes. Executing that text faithfully requires:

| Dependency | Actual source behavior | Current gap |
| --- | --- | --- |
| Aliases | `groovy/prePostInit/Recipemaps.groovy` initializes static fields by looking up the recipe-map catalog, not only the two maps subsequently used | Native map catalog and actual GroovyScript mapper/binding bootstrap |
| Map shape | `groovy/preInit/MetaClassExpansions.groovy` installs field setters; `groovy/prePostInit/ModifyRecipeMaps.groovy` mutates maps and removes recipes | Native phase ordering and preserved mutations/removals |
| Callback | Susy-Core `SuSyRecipeMaps.init()` installs the MIXER-to-BLENDER builder callback | Actual callback execution and final registration lineage, not just the existing field projection |
| Materials | GT materials, Susy-Core materials, and the pack's `material.SuSyMaterials` are different producers | Material registry events, priorities, property/flag changes, registration freeze boundaries |
| Item/fluid identities | Material declarations precede generated meta-items, fluids and ore registration | Executed generation, actual identities and ordered ore membership |
| Competing recipes | Other producers and later mutations can change the final map | A declared selected-program boundary; full-pack selection cannot be inferred from one file |

The existing source package captures much of the relevant source, but capture
completeness is not executable dependency completeness. Loading mod entry points
indiscriminately, skipping static initialization, or creating placeholder maps
would conceal rather than close these gaps.

## Concrete registry origins to close first

These are declaration origins, **not a recovered registry**:

- `dustTinyBorax`: GT `SecondDegreeMaterials.java` declares Borax; the small
  dust form depends on ore-prefix/material generation and registration.
- `dustSodiumHydroxide`: GT `FirstDegreeMaterials.java` declares SodiumHydroxide.
- `dustTinyMercaptobenzothiazole`: pack
  `groovy/material/OrganicChemistryMaterials.groovy` declares Mercaptobenzothiazole.
- `ethylene_glycol`: the same pack material class declares EthyleneGlycol.
- `polydimethylsiloxane`: GT `OrganicChemistryMaterials.java` declares
  Polydimethylsiloxane; later pack property changes and fluid generation still
  have to be included.
- `coolant` and `advanced_coolant`: Susy-Core's
  `supersymmetry/common/materials/SuSyUnknownCompositionMaterials.java` declares
  the two liquid materials through `Material.Builder`.
- `dyeCyan`: Forge's ore dictionary can receive multiple mod registrations.
  A hard-coded single vanilla dye is not an adequate replacement for membership
  and registration order.

There is a concrete reason not to manufacture even an apparently harmless ore
placeholder: GroovyScript `OreDictIngredient.getAmount()` returns **zero when
its ore membership is empty**. GT `RecipeBuilder.ofGroovyIngredient` reads that
amount when creating `GTRecipeOreInput`. Registry state therefore affects the
constructed recipe itself, not only a later matching query.

## Implemented native operation

The [material-property kernel](material-properties.md) is now an implemented
bootstrap prerequisite. It retains native property verification, cross-material
dependency effects and the material-level phase predicate in an explicitly
restricted domain. It does not create registries, dispatch material events,
generate fluids/items or broaden the recipe endpoint. Its source extraction and
48,000-operation comparison are documented separately.

The [registry lifecycle and fluid queue](native-registries.md) now retain GT
registry transitions and nontransactional entry behavior over original pinned
Minecraft utility bytecode, plus FluidStorageImpl orchestration over explicit
dependency ports. Separate source comparisons cover those bounded extractions.

The [native fluid domain](native-fluids.md) now connects selected actual
Material.Builder operations, FluidProperty and FluidBuilder to native scalar
fluids, registration and lookup. The locked Susy-Core unknown-composition
initializer constructs all ten declared materials in an explicitly isolated
universe, including Coolant and AdvancedCoolant. It does not execute material
events, the full pack bootstrap, ore generation or Coolants.groovy.

The [native material-event lifecycle](native-events.md) now executes selected
Cleanroom dispatch/transforms and GT registration phase ordering with explicit
producer ports. It shares one material property store with the fluid domain.
Actual pack listeners/producers and generated-registry qualification remain open.

The [expanded material construction domain](material-construction.md) now retains
the full GT builder, composition/element metadata, all built-in property keys and
flags, marker interning and the additional material property implementations.
A separate source comparison exercises the complete GT element producer and a
bounded source edit. This does not close later producers, ore-prefix effects,
enchantment identities, addon transforms or the generated registry.

The [native prefix and marker domain](native-prefixes.md) now retains all 101 GT
prefixes, 65 icon types, native dye identities, marker initialization and prefix
state/handler behavior. Source comparisons use explicitly synthetic material
field inputs and both unique-stone configurations; full material/config producers
are not substituted by those fixtures. A real Groovy fixture tests typed calls
and closure predicates without widening the public source-execution surface.

`BuilderValidation` extracts GT `RecipeBuilder.validateGroovy` and
`getRequiredString`, retaining their conditional checks, evaluation order and
message text. Its list, map-shape and lazy-message carriers replace the enclosing
game-dependent class. Those carriers do not decide item existence, ore
membership, property validity, registration or machine acceptance.

The bounded AST checker now uses this operation instead of its former combined
boolean check. It still obtains map shapes from the old explicit source
projection, and it still requires an explicit-context registry. It is not the
new native construction pipeline.

The source-conformance driver independently reads the locked original file,
extracts the two complete methods and executes them on the selected JVM. It
compares 20,000 vectors including diagnostic ordering, zero/negative EU/t,
nonpositive duration, empty fields and changing limits. This checks the
extraction and carriers; it does not independently establish whole-game parity.

## Execution isolation

`Main` retains one disposable worker per request. The current local default is
Bubblewrap. Workbench can explicitly select Docker's `runc` runtime or Docker
with gVisor's `runsc` runtime through Core's sandbox host. Core checks the local
daemon/runtime, provisions a digest-pinned Linux x64 base image, and owns
session cleanup. The OCI worker mounts the selected Java runtime, input files
and classpath read-only, uses a private temporary filesystem and network, drops
capabilities, uses the invoking user's numeric identity and groups to read
private profile inputs, and has no Docker socket mount. The
backend and policy are recorded with the invocation. Neither backend silently
falls back to another when unavailable.

Before request parsing or compilation, `WorkerIsolation` installs Linux x86_64
seccomp with required thread synchronization. Existing JVM threads receive the
filter. Process forks/replacement, network sockets, cross-process memory access,
namespace/mount changes and alternate kernel execution mechanisms are denied.
`clone3` returns ENOSYS so libc can use the inspected `clone` path; only thread
creation is allowed there. The filter is installed after Java starts, so it does
not need to allow a later executable launch.

The exact `socketpair(AF_UNIX, SOCK_STREAM, 0)` operation is permitted for the
selected JDK's private pre-close descriptor. Other socket-pair forms and
socket/connect/bind/listen remain denied. Cold native networking therefore raises
its ordinary I/O denial rather than failing JDK class initialization. BOP's
original remote-trail callback retains its own offline warning/fallback; the
worker does not acquire internet access or prewarm socket classes before isolation.

Workbench resource targets are temporarily suspended for MVP development. The
worker inherits host CPU/file/descriptor/address-space limits and uses JVM
resource ergonomics. Core dumps remain disabled. Cancellation and fresh workers
remain required; optimization and budget selection follow MVP.
An unavailable kernel policy or selected container runtime fails admission. A terminated worker with no result
is incomplete evaluation, never native recipe rejection. These are Linux-specific
host protections, not a sandbox for the caller's in-process Java library use,
loaded-memory attestation, or proof against a hostile JVM/kernel.

Controlled subprocess tests cover native Groovy helper compilation, independent
worker static state, thread creation, hidden host files, read-only explicit
inputs, cold network denial on an existing thread, exact private socket-pair
admission and wrong-form refusal, subprocess denial, heap limits,
the actual kernel soft/hard resource values, and a compiler-time AST
transformation attempting to launch a process. These
fixtures are not Supersymmetry registration evidence.

## Remaining implementation order

1. Execute the source-bound material registry lifecycle without Minecraft
   startup: GT registry creation, material events, Susy/pack producers, property
   changes, finalization and generated item/fluid/ore registrations. Trace actual
   external dependencies such as Supercritical rather than replacing them.
2. Package those executable dependencies under the Supersymmetry profile, with
   their original licenses and exact input identities. Core remains the
   provisioner/process owner. No game/mod binaries enter the public source tree.
3. Execute the actual GroovyScript bindings, aliases, map mutations and Coolants
   script against that state. Preserve effects before failures and source lineage.
4. Feed the resulting objects into the admitted machine boundary, and demonstrate
   baseline/edit differences, upstream rejection and incomplete dependencies.
   Close competing producers before making any full-pack selected-recipe claim.

A native recipe-chain capability requires these outcomes to be demonstrated
without a caller-supplied synthetic registry. An unimplemented placeholder is
not a public capability.
