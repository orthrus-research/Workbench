# Registration inputs and lifecycle declarations

Status: binary declaration inspection is implemented. Registration activation,
dispatch order, callbacks and material/registry execution are **not** qualified.
The earlier modeled event and class-loading execution backends have been removed.
The [native JVM direction](native-runtime.md) will use actual upstream operations
where independently executable. [Source research](registration-research.md) and
declaration inspection remain useful; neither establishes callback execution.

The [offline artifact bundle](artifact-inputs.md) supplies hash-verified bytes.
Axiom now inspects method declarations as well as class declarations, without
loading mod classes, evaluating method bodies or starting a game.

## What the inspector provides

Every inspected JAR reports `eventSubscriberClasses` and `eventHandlerMethods`.
An explicit `artifactPaths` detail selection additionally returns:

- `eventSubscriberDeclarations`: classes directly annotated with Forge
  `Mod.EventBusSubscriber`, including explicit mod ID and physical-side values.
- `eventHandlerDeclarations`: owning class and methods directly annotated with
  Forge `SubscribeEvent` or `Mod.EventHandler`.
- Method name, JVM descriptor, access flags and generic signature where present.
- Explicit annotation values, including priority, cancellation and `SideOnly`.
- Original class-entry name/hash, superclass, direct interfaces and class-level
  annotations relevant to these declarations.

Descriptors distinguish overloaded methods. Generic signatures preserve the
declared item/block/recipe type of `RegistryEvent.Register<T>`; the erased JVM
descriptor alone does not retain that distinction. Static/instance flags are
retained, not treated as automatic proof that Forge will register the method.

Omitted annotation members remain omitted. `annotationDefaultsResolved: false`
means the declaring annotation classes and loader rules have not yet supplied
resolved defaults for those usages. Explicit [platform inspection](platform-inputs.md)
now reads annotation type members/defaults from original classes, including
SubscribeEvent priority/cancellation and EventBusSubscriber side/mod-ID defaults.
Usage-provider resolution and default application remain separate work.
Side annotations alone do not turn a method into an event
handler. Inherited handlers, programmatic registration, dynamic closures and
transformed methods require additional work. Discovery order is archive order,
**not event dispatch order**.

The pinned default-client artifact selection contains 353 direct automatic
subscriber class declarations and 3,325 annotated event/lifecycle methods. These
counts include unqualified declarations; they are not active subscriber counts.

## Source-traced material initialization

The following relationships are observed in the exact repositories selected by
the [source lock](../sources/supersymmetry.lock.json). They do not establish that
all injections, listener registrations or dependencies are active in a selected
installed platform.

1. GroovyScript's `core/mixin/LoaderControllerMixin.java` injects at the head of
   `LoadController.distributeStateMessage`. On `PREINITIALIZATION` it calls
   `GroovyScript.initializeGroovyPreInit()`. Its other branches run the Groovy
   `INIT` stage at `POSTINITIALIZATION` and `POST_INIT` at `AVAILABLE`.
2. `GroovyScript.java` initializes mappers and mod support, binds those mappers,
   and runs the configured pre-init scripts. Its construction handler also
   registers itself and another listener class on the Forge event bus. Annotation
   discovery alone cannot reconstruct these registrations.
3. GT's `gregtech/core/CoreModule.java` material sequence posts
   `MaterialRegistryEvent`, unfreezes registries, registers base GT materials,
   sets the fallback material, and posts `MaterialEvent`. It then closes new
   material registration, posts `PostMaterialEvent`, and freezes the registries
   before ore unification and item/block/fluid initialization.
4. Susy's `supersymmetry/common/CommonProxy.java` declares HIGH-priority handlers
   for both material events. They initialize Susy materials and then add ore
   prefixes, material flags and generated-fluid behavior respectively.
5. Pack `groovy/preInit/MaterialChanges.groovy` registers a LOWEST-priority
   `MaterialEvent` closure. It adds the fiber base property, calls the pack's
   `material.SuSyMaterials.init()`, then `classes.ChangeFlags.init()`. This is
   distinct from Susy-Core's Java `SusyMaterials` class.
6. Pack `groovy/preInit/RegisterMetaItems.groovy` registers a
   `PostMaterialEvent` closure that constructs the pack's extra metaitems.
   The corresponding `GroovyEventManager.listen(Closure)` overload explicitly
   supplies NORMAL priority and the main event bus in the captured source.

Consequently, neither a sorted list of material files nor a list of annotated
Java methods is an executable material universe. Closure type resolution,
registration time, event priority, same-priority ordering, cancellation,
inheritance, mutable configuration and actual loader/mixin composition matter.
GCYM and Supercritical material/property dependencies must also be accounted for.

## Independent checks

`tools/axiom_artifact_smoke.py` compares the extracted event/lifecycle methods of
five original binary classes against the JDK's `javap -p -v` reader:

- `gregtech.GregTechMod` and `gregtech.common.CommonProxy`.
- `supersymmetry.Supersymmetry` and `supersymmetry.common.CommonProxy`.
- `com.cleanroommc.groovyscript.GroovyScript`.

All 41 annotated methods in those classes agree on name, descriptor, access,
generic signature and the admitted method annotations. This is an independent
declaration comparison, not execution conformance. Unit cases additionally cover
explicit sides/cancellation, absent defaults, generic signatures, non-handler
methods, malformed headers and manifest ambiguity.

## Next executable boundary

Bind the platform and its libraries explicitly before qualifying lifecycle
execution. The captured pack declares Forge 14.23.5.2860 / Minecraft 1.12.2;
Workbench's separately pinned Cleanroom platform is not an implicit equivalent.
Its exact bytes, loader rules and transformation composition must be selected
and verified if used as the Axiom target.

Then resolve annotation defaults, inherited and programmatically registered
listeners, event type/generic filters and actual dispatch semantics. Execute the
material/bootstrap callbacks against independently represented domain state,
closing the real helper and dependency paths instead of substituting a fixture
registry. Missing or denied operations remain explicit gaps. No Minecraft launch
or game-harness result supplies the missing authority.
