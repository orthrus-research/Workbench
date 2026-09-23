# Native material event lifecycle

Status: **implemented internal lifecycle dependency, not a qualified pack registry**.
The full source-built Coolants dependency-registry vertical remains unfinished.
No `bootstrap` endpoint or new recipe-validity claim is exposed by this checkpoint.

## Native execution and source retention

`nativeevents/` retains the selected Cleanroom event classes and both event
transformers at `fe78db8dc4fcee47df7549230858f4c064208d73`. Source identities are
in [cleanroom-events.lock.json](../sources/cleanroom-events.lock.json). Extraction
relocates packages and ASM descriptors and supplies explicit loader-owner,
logging and bytecode-transformer interface ports. It does not rewrite dispatch,
substitute a custom JVM or start Minecraft/LaunchWrapper.

The selected JVM executes reflection, `LambdaMetafactory`, Guava's hierarchy
and weak maps, and the selected ASM transforms directly. Every `NativeEventSpace`
has its own event statics and owns the transformer classes, so recursive parent
loading sees the same candidate definitions. Transform input/output digests are
retained. Candidate classes cannot replace engine/JDK definitions. This class
loader is **not a security boundary**: untrusted execution still requires the
existing isolated worker. Compilation and event tests do not establish installed
mod discovery, side transformations, active mixins or source/binary equivalence.

Library JAR hashes are checked against the event lock before creating a space.
Guava's declared transitive failureaccess version differs from the selected
Cleanroom platform: Axiom explicitly uses the platform's **1.0.2**, not the
newer Maven default. Guava 33.6.0-jre, JSpecify 1.0.0, ASM/ASM Tree 9.10.1 and
Log4j API/Core 2.26.0 are also explicit, locked native dependencies. Logging is
directed to stderr so diagnostics cannot corrupt the JSON stdout protocol.

## Behavior that must not be normalized

- Same-priority child listeners precede parent listeners. Registration order
  within a list is retained; reflective method order is not alphabetized.
- Priority sentinels advance event phase; phase must increase strictly.
- Dispatch uses a cached listener-array snapshot. Mutations affect subsequent
  posts without rewriting the active snapshot.
- Generic filters compare native `Type` identity, not string names or equality.
- Cancellation/result annotations require the original event-subscription
  transformation. It also generates subclass listener lists and missing no-arg
  constructors. Subscriber transformation publicizes eligible methods/classes
  but rejects private annotated methods.
- Registration catches and logs `Exception` from handler construction; it does
  not convert every failure into a propagated exception. Dispatch failures retain
  their exception-handler index and prior effects.
- Context-setting events restore their prior owner only on successful return.
  Upstream has no `finally` restoration after a throwing listener. Its optional
  Log4j mod-context removal has the same failure boundary.
- Canceling a non-cancelable event throws `UnsupportedOperationException`.
  Disposing an already-built listener list can leave a null cache, not an empty
  array. These observations are retained, not repaired.

## Material registration sequence

[material-lifecycle.lock.json](../sources/material-lifecycle.lock.json) binds the
three actual GT event declarations and the material-registration block in
`CoreModule.preInit`. `MaterialLifecycle` retains that block with explicit ports
for marker initialization, the GT material producer and its Aluminium identity:

1. Initialize marker registry; post `MaterialRegistryEvent` while registries are PRE.
2. Unfreeze; construct `MaterialEvent`; execute GT material registration; set
   the actual GT Aluminium fallback.
3. Post addon material event while OPEN.
4. Close new registration; post `PostMaterialEvent` while CLOSED, allowing
   modification of existing material properties.
5. Freeze before subsequent block/item/tool/fluid generation.

The wrapper requires one fresh registry universe. Exceptions leave prior effects
and the reached phase intact; a failed lifecycle is not retried or rolled back.
Marker and material ports have **no default empty implementations**. Tests supply
explicit controlled producers, not pack material evidence.

The material carrier now owns one property container shared by fluid and material
operations. There is no second hidden `nativeProperties` store. Material source
and native fluid conformance continue to bind this change.

## Qualification evidence and remaining work

`tools/axiom_event_sources.py` and `tools/axiom_bootstrap_sources.py` check retained
source against immutable Git objects. `tools/axiom_event_conformance.py` compiles
the original Cleanroom event sources unchanged, using temporary declared host
ports, and compares independent native class spaces with the engine. Controlled
scenarios cover inheritance, cancellation, generics, cache mutation, ownership,
registration failures, subscriber transforms, static listeners, shutdown and
phase rules, plus 8,000 seeded listener mutations with parent/child cache
observations. Compilation, transformation and dispatch are also tested after
the real worker's kernel isolation policy is installed. These checks are not
a substitute for executing actual pack listeners.

Still required within the approved vertical:

- Complete GT/Susy/pack material construction and marker initialization, including
  configuration-aware Supercritical dependencies and effective GCYM/Susy mixins.
- Native Groovy event registration and the full relevant listener universe.
- Actual generated meta-items, vanilla/mod fluid bootstrap and ore membership,
  including all admitted `dyeCyan` contributors and registration order.
- Candidate source-edit/provenance tests, source/binary/transform correspondence,
  and a separately admitted executing bootstrap operation. Existing `target`
  inspection remains non-executing.

Until these close, `dustTinyBorax`, `dustSodiumHydroxide`,
`dustTinyMercaptobenzothiazole`, `dyeCyan`, `ethylene_glycol`,
`polydimethylsiloxane`, `coolant` and `advanced_coolant` are **not a qualified
source-built dependency registry**. Successful synthetic listeners or isolated
producer counts cannot establish that result.

## Additional producer source findings

The pack's GCYM 1.2.11 and Supercritical 0.2.7 tags resolve to
`54168e0dc6d55089469be43eb4ab7517776972b2` and
`4e143241bdadb1807818ecf984539ad7ccc6cb64`, respectively. These are reference
source selections, **not source-to-installed-binary equivalence** and not an
implicit expansion of the four-repository target package.

The selected pack `config/supercritical.cfg` declares `disableAllMaterials=true`
and `enableMaterialModifications=false`. At its selected tag,
`SCMaterials.register()` creates Corium before testing the former flag, then
returns without its four ordinary material groups. `SCEventHandlers` still calls
`SCOrePrefix.init()` afterward. Susy's construction handler also forces material
modifications off. Actual config loading and these effects must be executed;
neither all-default Supercritical materials nor an empty Supercritical universe
is faithful to the declared pack.

GCYM's HIGH material listener normally initializes GCYM materials. Susy's GCYM
mixin cancels that method and injects molten-generation flag changes before
GCYM post-material fluid generation. Resolving those exact transformations is a
required dependency, not permission to skip GCYM or guess its final registry.
