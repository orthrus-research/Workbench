# Original SERVER root class space

Axiom has a separate, source-bound check for original Cleanroom root setup over
raw Minecraft SERVER inputs. It executes the original root plugin callbacks and
observes their patch/remapping state without starting Minecraft. A successful
root-stage check remains an incomplete material-initialization result.

The [root input policy](../../../profiles/platforms/cleanroom/native-root-class-space.json)
selects the complete original Cleanroom 0.6.12-alpha universal JAR and Minecraft
1.12.2 SERVER JAR. The [source lock](../sources/native-root-class-space.lock.json)
binds original Cleanroom and Foundation source revisions. Original resources and
JAR CodeSources remain available to native setup and certificate checks.

| Input or observation | Native meaning |
| --- | --- |
| Complete raw SERVER JAR | Original obfuscated bytes for SERVER binary patch checks. |
| Complete universal JAR | Original FML classes, binary patches, mappings, access rules and metadata resources. |
| Original root plugin wrappers | Actual FML/Forge root registration, transformer registration, injection, setup hook and container-name append. |
| Complete selected patch inventory | Actual nonempty SERVER patch membership, checksum identities and patch bytes match the retained input descriptor. |
| Five class byte witnesses | Original raw, patched, remapped and access-transform stages for Block, ItemStack, EnumFacing, Vec3d and Enchantment; these classes are not defined by the probe. |

`FMLServerTweaker.acceptOptions` establishes the offline native options and derives
the root JAR location from its actual CodeSource. A separately named method in the
exact original `CoreModManager` retains the contiguous `handleLaunch` prefix
through both original root registrations and its corruption check. The seam stops
before extra classpaths, command-line coremods and broad discovery. The original
`handleLaunch` method remains intact. This root seam is an alternative entry for
a fresh worker; it never follows the existing environment-only seam in one JVM.

Original `FMLPluginWrapper.injectIntoClassLoader` invokes the full
`FMLSanityChecker.injectData` and `call`. Native SERVER selection, patch checks,
remapper resource loading and certificate behavior remain intact. Missing patch
resources or uninitialized maps cannot qualify merely because a native method
returned. The check does not install development flags or ignore integrity checks.

The generated `root-class-space.json` retains artifact/resource hashes, the
complete selected patch inventory and per-stage class hashes. It supplies byte
witnesses, not a replacement class image. The separate root package validates
every retained file and explicit classpath entry before native execution.

`tools/axiom_native_root_stage.py` builds this package and invokes the trusted
supervised probe. Original execution uses the existing fresh worker boundary:
MVP resource targets are temporarily suspended. The worker creates its own disposable home. Native output is kept off the
protocol stream, and original failure causes survive observation failures.

The result uses operation `native-root-stage`, status `incomplete`, and a separate
`result.rootStageReady` observation. Readiness requires both original root
callbacks, native option/home initialization, original container/tweak lists,
the selected patch inventory and all five exact class witnesses. A root failure
is a platform prerequisite failure; candidate compilation has not started.

The existing material lane still consumes its separately pinned prepared CLIENT
images in a SERVER worker. Those images are not used as raw input in this root
check. This root result does not qualify that earlier side/stage composition or
replace its image policy.

## Early-stage implementation under qualification

`tools/axiom_native_root_stage.py --stage early` selects a separate fresh context;
its default `--native-context root-only` starts with an empty disposable home.
It extracts the exact original Foundation bootstrap and tweak loop into an added
method, stopping before launch-argument callbacks or target resolution. The
original launcher method remains intact. Its fatal logger call is retained; the
added method propagates the original caught exception instead of exiting the
worker before it can report that cause.

The extractor uses the bridge classloader's ASM. Native ASM remains available
for Foundation's original transformation setup. The early context invokes the
original server bootstrap, library discovery, root wrappers, sorting, Mixin and
MixinExtras initialization, and complete `FMLDeobfTweaker` transition. It requests
INIT-phase definition witnesses for Vec3d's SERVER patch methods and EnumFacing's
public access-transformed fields, with actual defining loader and CodeSource.
Readiness checks the original ordered transformer queue, the five generated FML
wrappers' actual parents and owner, and CleanMix's inactive PREINIT/active INIT
proxy instances. Transformer name membership alone cannot qualify this transition.
Native warnings and errors survive server logging reconfiguration; logged errors
and diagnostic overflow prevent readiness. Diagnostic retention includes logger
names, metadata and JSON escaping in its byte bound. Overflow preserves earlier
complete records and marks the observation incomplete without throwing from the
native logging call or retaining a partial trace.

The profile-owned
[required early context](../../../profiles/packs/supersymmetry/src/workbench_profile_supersymmetry/axiom-native-early-context.json)
adds seventeen complete original JARs: Bubbles, CodeChickenLib, GeckoLib, GCYM, GTCEu, GTFO,
GroovyScript, Immersive Railroading, IVToolkit, ModularUI, OpenComputers, Scalar,
Supercritical, SussyPatches, Susy-Core, TrackAPI and Universal Mod Core. Cleanroom supplies the integrated
MixinBooter and ConfigAnytime facilities. CodeChickenLib satisfies the original
GTCEu dependency checked by Loader selection. OpenComputers supplies the DriverItem
API resolved during Susy-Core definition; Scalar supplies its original Scala
libraries and language adapter. GeckoLib and ModularUI satisfy original Susy
subscriber definition interfaces. Immersive Railroading and Universal Mod Core
supply its original construction callback operations; TrackAPI is an original
required IR dependency. Select this context through the same
builder and supervised probe:

```sh
python3 tools/axiom_native_root_stage.py \
  --stage early --native-context supersymmetry:required-early \
  --java-home PATH_TO_SELECTED_JDK --engine-home PATH_TO_ENGINE \
  --library-root PATH_TO_ORIGINAL_LIBRARIES --server PATH_TO_RAW_SERVER_JAR \
  --cleanroom PATH_TO_CLEANROOM_SOURCE --foundation PATH_TO_FOUNDATION_SOURCE \
  --addon-inventory PATH_TO_COMPLETE_ADDON_INVENTORY \
  --pack PATH_TO_PACK_SOURCE --program PATH_TO_SAVED_PROGRAM_ZIP \
  --output NEW_OUTPUT_DIRECTORY
```

The [addon inventory](native-addon-discovery.md) must use
`--candidate-inputs complete-artifacts`. The builder rechecks each required JAR
against its exact pinned pack descriptor and retains original filenames, manifests
and resources. `--pack` supplies those immutable descriptor objects; `--program`
supplies the developer's complete saved `groovy/` and `config/` archive. Every
submitted source and configuration byte reaches the disposable home before native
bootstrap. Complete original mod artifacts remain in verified read-only input
storage, exposed under their original names by links in that fresh home; large
immutable JARs do not consume the worker's per-file write allowance. Their real
CodeSources remain bound to the verified originals. Saved-source capture has an
independently computed source digest/count acknowledgement.
Run-config order and configuration resources are preserved. Candidate Groovy
compilation and script execution have not started at this boundary.

Declared contained artifacts remain the original loader's responsibility. After
native extraction, Axiom verifies the selected member in its pinned parent JAR,
the extracted bytes and the exact path inside the fresh worker home. Callback and
transformer CodeSources must match that verified extraction. Missing or altered
members, escaping paths and unexpected coremods remain incomplete context;
observation does not extract dependencies or register them with a classloader.

Required-context readiness checks every profile-declared original coremod callback,
including the two roots, the exact ordered transformer queue, generated wrappers
and injected container names. It also requests definition of Groovy's `CachedClass$1`
through that queue and observes the original `makeFieldsHook` transformation,
with original input bytes, actual defining loader and CodeSource bound. The class
is defined without requesting initialization or invoking its field-producing method.
Exact Loader field observations avoid reflective enumeration that would resolve
other field types. Readiness requires `LoadController` to remain undefined through
all INIT observations so its DEFAULT mixin target is not loaded prematurely.

Both early contexts stop in native `INIT`. The required Groovy transformer witness
does not establish application of GroovyScript's mixin configuration targeting
`DEFAULT`; late mixin selection and later construction remain open. Captured
configuration is an input guarantee, while qualification of all affecting native
configuration consumers remains separate.

The preceding fifteen-artifact context passed bounded early-stage acceptance on
the selected Temurin JVM, using the complete saved Groovy/configuration archive.
The positive run reaches INIT and verifies original callbacks, ordered transformers,
injected containers and all three actual class-definition witnesses. The existing
conformance tests also verify wrong side/stage, missing/changed original artifacts,
native configuration failures, removal of a required transformed method, retained
logged errors after callback completion and recovery after saved correction.
Original native causes are retained; an early linkage gap remains incomplete
context rather than a qualified source-invalid verdict. See the
[test procedure](../tests/README.md#early-native-bootstrap-qualification).

This acceptance does not qualify later mixin application, selection, construction
or installed material checks. Results remain explicitly incomplete for material
initialization. The default root-stage check and existing material command retain
their separate boundaries.

## Native selection continuation

With the required context and the same input arguments, `--stage selection`
continues the original early path. It executes each completed tweaker's original
launch-argument callback, allowing the native environment tweaker to enter DEFAULT.
It then executes complete vanilla registration and the original SERVER handler
constructor before Loader selection, without invoking a game entrypoint.

An added method retains the complete original `Loader.loadMods` prefix through
late-mixin setup and stops before the construction transition and event dispatch.
The original method remains intact. Extraction verifies its complete method
fingerprint after ordinary native transformations, so unrelated transformed class
bytes cannot stand in for the selected original Loader behavior.

The unchanged saved baseline reaches this boundary with native discovery,
built-in/injected containers, required dependencies, active order, configuration
loading and queued late mixins observed. The original missing-patch advisory is
retained; game-start confirmation is outside this initialization scope.
The result remains `incomplete` for material initialization. Bounded
`selectionReady: true` additionally requires actual DEFAULT definitions of
LoadController and OreDictUnifier through their original JARs. Passive observers
retain original input and definition hashes, defining loader, MixinMerged owners
and inserted calls. GroovyScript's lifecycle dispatcher must call its injected
hook; Susy's overwritten registration method must call the original recycling
handler. These are definition observations, not execution of those later hooks.

The profile binds required native containers, late configurations and witness
methods. Readiness also requires the real active order, loaded state, no user-mod
instances, complete diagnostics without errors, and LoaderState.LOADING after
definition observation. Construction, candidate compilation and required-stack
material/recipe initialization have not been dispatched.

Native acceptance preserves the complete unchanged saved baseline and checks a
disabled required dependency, original coremod blacklist, optional late-mixin
configuration, removal of a required transformed hook and saved correction in a
fresh worker. Queue membership without the actual hook cannot pass. This qualifies
the declared selection boundary; further targets and registration effects remain
obligations of the later native lifecycle and installed workflow.

## Original construction boundary

The same supervised tool accepts `--stage construction` with the required context.
It adds a copy of the complete original `Loader.loadMods(List)` method, retaining
all instructions, locals, frames and exception regions. Two observations mark the
end of original late-mixin setup and entry into original construction dispatch.
The selected method fingerprint is checked after native transformations; the
original method is unchanged. Selection is executed once with its actual locals
and event arguments, rather than replayed through another bootstrap path.

The method's final PREINITIALIZATION transition is a state boundary. It does not
dispatch the later `preinitializeMods` event or run Groovy preInit scripts. Original
Groovy construction creates its sandbox before this boundary. The observer keeps
the earlier selection evidence, actual container states, original mod instances
and CodeSources, native failure causes with bounded frames, and aggregate native
cache/heap measurements. Cache measurements do not clear state or request GC;
measurement failures are recorded separately from the original construction result.

Construction alone does not establish the later Groovy or material boundary. The
unchanged saved baseline now returns from original construction with all thirty
selected containers CONSTRUCTED and Loader at PREINITIALIZATION. The original
Groovy sandbox and engine, Mods/Log/EventManager bindings, and Bubbles, GT, Susy and
SussyPatches external plugins are observed. Susy's original callback adds its two
IR stock loaders and disables Supercritical material modifications.

ModSupport remains unfrozen and Groovy's script owner remains unassigned at this
boundary. The original PREINITIALIZATION mixin hook initializes those facilities
and runs saved preInit scripts. The Groovy continuation below executes that hook.
The standalone construction checker keeps the later Groovy boundary unestablished;
its return alone cannot qualify script execution.

## Groovy initialization continuation

`--stage groovy` continues the same required native context through the original
PREINITIALIZATION hook. Before native classloader creation it executes the pinned
Foundation VM-preparation method. It then preserves Loader's original preInit
state check, registry creation, object-holder discovery and capability injection.
A separate copy of the transformed LoadController dispatch head invokes Groovy's
original injected hook and stops before the FML preInit event. Original methods,
phase/event operands and exception behavior remain intact.

The Loader method pin distinguishes raw bytecode from the original deobfuscation
of its deferred ResourceLocation lambda. Neither representation is a prepared
native runtime image. The original sandbox retains its compiler/customizers;
existing source, bytecode, trait and mapper admission is reused for saved scripts.
No temporary mod owner or manually populated compatibility state is supplied.

The unchanged saved baseline reaches the original hook return with ModSupport
frozen and the actual injected Supersymmetry script container assigned. Bubbles
supplies the Baubles API used by saved Battery source; GTFO supplies material
classes imported by the unchanged material sources. Their original coremods,
access transformer and mixin selection participate in the native pipeline.
The saved preInit phase now compiles and returns without observed native errors.
Both Log4j and Groovy file-only diagnostics use the existing source-location
observer, and the original engine index records reached script definitions and
preprocessor outcomes. The result remains incomplete for material initialization.
The Groovy probe records at most 64 KiB of console beginning/end; any omitted
middle bytes are explicitly reported and prevent qualification. The worker's
existing JSON, CPU, wall, write and temporary heap allowances remain unchanged.

The production pack `material-program` command and this Groovy probe share
`OriginalNativeProgram`, which creates the same fresh original class space. The
pack runtime requires raw SERVER artifacts and cannot fall back to the prepared
bootstrap; the separate bounded GT-base context retains its regression route.
Runtime assembly excludes saved scripts, so each command uses its newly supplied
complete archive. Native diagnostics and indexed-script observations enter the
existing result and source-location projection.

The Groovy checker requires verified original constructed instances, profile-owned
external plugins and Susy construction effects, then original script ownership,
complete indexed-source execution and both native diagnostic channels. It can
qualify this bounded stage while the envelope remains incomplete for material
initialization. The Groovy probe stops at this boundary; the production command
uses the complete preInit continuation below.

## Complete preInit continuation

The native `--stage preinit` continuation uses a fresh worker and a complete copy
of original `Loader.preinitializeMods`. Its transformed controller copy preserves
the original Groovy hook and FML event dispatch. Passive observations record the
Groovy boundary, mod preInit return and non-recipe registry-event return. Original
methods remain unchanged. The complete continuation refuses when either prefix
is already installed, and native stage selection installs one route per worker.

The declared context reaches `INITIALIZATION` with every selected container
`PREINITIALIZED` and one mod preInit dispatch. Native warnings remain visible.
Console and native diagnostic capture retain complete evidence without target
allocations during MVP development. Console text uses lossless gzip/base64 JSON in the existing snapshot
encoding (`consoleEncoding: gzip-base64-json`); completeness, original byte count
and full-stream digest stay visible. Java and Python expansion restore the exact
head/tail text. Omitted console bytes or diagnostic rows still prevent acceptance.
Resource budgets and optimization follow MVP.

After original preInit returns, the shared observation helpers read the existing
material manager, attached properties, custom meta-item instances and Forge fluid
registry through their original types and getters. They do not create a manager,
replay registration or execute prepared runtime images. The result includes complete
current material/fluid membership catalogs with selected-state fingerprints, custom
item ownership and Forge identities, and readable Iron/Diamond property witnesses.
Attached fluid properties retain original storage, primary-key, completed-queue,
material lookup and Forge-fluid identities. Catalog completeness describes the
observed collections, not complete initialization.

Passive generated-content observations also cover the original prefix-item,
material-block and ore-block collections. Fingerprints retain actual variants,
native material ownership and Forge membership. Selected Iron/Diamond witnesses
include original stack getters and block metadata/state/property round trips.
Exact native method descriptors avoid resolving unrelated CLIENT-only signatures;
they do not change the original methods, artifacts or classloader.

Material catalogs use the original registry's named collection. Prefix-item
observations additionally verify numeric-iterator membership and preserve any
difference from named lookup. The selected Susy source registers two distinct IDs
under `susy:kreep_basalt`; original GT registration retains both numeric entries
while replacing the named entry. The observer retains both identities and selected
states without correcting this native behavior.

Generated Forge membership and current ore-dictionary/unifier lookup are separate
observations. In the selected GT source, `CommonProxy.registerRecipes` populates
generated item/block ore-dictionary entries during the original recipe registry
event. Missing generated-form unifier lookups before that event remain visible;
R6 owns that continuation. No recipe callback is replayed early to fill them.

The preInit envelope uses `axiom.native-stage-snapshots.v1` to retain earlier stage
fields by reference when their values are identical. Its two native diagnostic
lists refer to the complete envelope diagnostic array. `NativeStageSnapshots.expand`
and the Python lane's `expand_stage_evidence` restore the original evidence without
changing retained receipts. Earlier differing warnings and failures remain distinct.
The optional `fingerprintEncoding: "base64url-sha256"` field encodes the five native
catalogs' SHA256 bytes without padding on the wire; expansion restores their exact
canonical hexadecimal fingerprints. Names, selected values and source evidence
are unchanged. Earlier envelopes without this field remain supported.
PreInit retains complete material/fluid/prefix-item/block catalogs without MVP
byte or entry-count targets. Observation failures remain explicitly unavailable;
no partial membership result can certify a complete inventory.

The normal material command now runs this complete preInit continuation. It exposes
the same current catalogs and selected witnesses, with saved material/form/fluid
changes and native failure/correction evidence. Its native snapshot references the
single execution-level effect/custom-item collections and diagnostic array;
`compactExecution`/`expandExecution` preserve the complete evidence without duplicate
catalogs. Additional bootstrap failure diagnostics remain separate from the original
native/Groovy channels. The bounded preInit checker now requires complete current
catalogs with their declared observation scope, consistent named registry membership,
completed material/fluid bindings, original custom-item owners and generated native
identity witnesses. Missing or partial evidence remains a named gap. Bounded stage
acceptance does not promote the full saved-scope or installed product verdict.
That preInit route does not execute recipe phases or start Minecraft.

Requested material observations reuse this one collection pass and the existing
native value/fluid observers. Original eligibility and world-free ordinary-drop
getters run only for requested facts or explicitly selected full observations;
they do not invoke initialization or recipe processing. Complete effect fingerprints
and the fixed Iron/Diamond witnesses remain available alongside selected projections.
The Java/Python execution snapshot expansion restores both sets of evidence.

## Remaining initialization boundaries

The [MVP contract](initialization-mvp.md) requires three distinct prerequisites
before the existing native content work can form a complete supported scope:

1. Qualify the original early transition, including `FMLDeobfTweaker` rename/
   access registration, queued native transformations, CleanMix initialization
   and original `Loader.injectData` / `Loader.instance` prerequisites. Define
   early-safe targets through the actual classloader and retain resource/CodeSource
   identity. The required context now has positive and affecting-failure acceptance
   for this boundary on the shared production Groovy path.
2. Establish native mod selection, container/API/dependency/configuration state
   and late-mixin conditions before construction dispatch. Early transformations
   must not preload targets that require the later native selection state.
3. Execute real construction and Groovy bootstrap, then the supported native
   material/item/fluid and recipe lifecycle. Root readiness does not establish
   real mod ownership, addon initialization or effective registration changes.

These boundaries must preserve original ordering and required dependencies; they
are not permission to manufacture loaded-state flags or recreate loader behavior.
SERVER/common execution does not qualify CLIENT-only behavior. No Minecraft
entrypoint, rendering, world or game loop is needed for the declared MVP scope;
if original dependencies require one, that is an unresolved architecture boundary.

Fresh Core-managed setup and the declared installed startup-edit scope pass with
Temurin 25.0.4+7 Linux x86_64. Recipe scope, measurements and final MVP qualification
remain open. This choice does not identify an upstream pack-installed JRE, and
root readiness alone does not qualify a runtime. The Cleanroom platform's
`cleanroom-provisional` variant and overall qualification label remain unchanged.

## Original recipe continuation

The same conformance tool accepts `--stage recipes` for the required context.
It completes original preInit, retains that startup snapshot, then invokes the
original `Loader.initializeMods()` on the same fresh native state. That method
owns Forge's recipe event, mod initialization, IMC, post-initialization, the
Groovy INIT/POST_INIT hooks and final registry freezing. It does not invoke a
Minecraft entrypoint. Native logged/thrown failures and saved-source causes remain
retained. The probe uses the current lossless engine output path.

This remains a diagnostic continuation. The pack policy admits original
`material`, `metaitem`, `item`, `ore`, `fluid`, `liquid` and `recipemap` bindings by
identity, native recycling and recipe read/remove/rebuild families, and selected
builder operations reached by the saved recipe scripts. Original Groovy bindings
also own the crafting, furnace and ore-dictionary roots. Admission checks exact public descriptor
families and preserves original overload selection, builder validation and effects.
The pack separately selects the original private GT recipe-map accessor used by
its recycling helper. Its exact declaring class, private instance visibility and
complete descriptor family must match; no accessibility or metaclass is changed.
Compiler Boolean/float/double conversion, numeric negation and range construction enter the
existing gate before calling the original Groovy helpers. Numeric operations
retain original results and exceptions while restricting operands to the selected
numeric types. Explicit numeric cast operands retain their original Groovy wrapper
and type constraint; admission reads the exact wrapper's stored numeric value and
refuses subclasses, nested wrappers and unselected values or type constraints.
Indexed assignment on the selected native map preserves original insertion and
return behavior for ordinary string/null keys. Reserved names, existing bean
properties and wrapped values cannot use that route to invoke metadata or
unwrapping callbacks.
Saved recipe-name construction uses the original string helpers;
replacement and string-list joining reject unselected formatting callbacks.
Concatenation also accepts the exact selected GT Material type, whose original
formatter returns its registry path; subclasses and unrelated formatting callbacks
remain refused for direct calls and native closure delegates.
Original `tap` callbacks on the selected recipe builders keep Groovy's closure
clone, delegate strategy and receiver return. Both direct calls and delegate calls
require a registered guarded source closure before the original helper executes.
The selected ore ingredient's `each` helper likewise runs its original iterator
and guarded source callback, preserving copied stack amounts, iteration order,
receiver return and native failures.
Original compiler default-argument bridges and captured-variable assignments
retain their native targets. Closures can call declared methods through their
unchanged compiler owner chain; changed delegates or resolution strategies do not
borrow that admission. Native bean metadata may expose an inaccessible implementation
through its public interface accessor, with the concrete method family still checked.
Mapped native methods may be inherited from an explicitly mapped superclass;
the concrete receiver still requires its own exact method-family admission.
Saved-source bean access uses the original metadata's public accessor only when
its declaring class and descriptor match an already guarded source method. It
does not invoke the accessor during admission or admit a metaclass replacement.
Exact selected static-field reads and metaclass method slots also preserve saved
console output and deferred native callback registration. Console output uses the
current native stream; callback registration does not invoke the game callback.
Selected numeric field writes preserve original mapped-field metadata and Groovy's
conversion/assignment path. The saved Netherrack hardness assignment and the
original ore registry's stored `ore_dict` alias use those native objects directly.
The ore observer distinguishes reload records from current Forge membership;
block hardness observations read stored fields without invoking block callbacks.
Groovy's original preprocessor exclusions remain visible in the script index and
do not count as missing applicable source. The observer does not re-run predicates
or force excluded CLIENT source to execute in a SERVER check.

The operation closure includes original pack-pinned dependencies required by saved
item/ore lookups. Original dedicated-server construction supplies the server owner
required by ICBM initialization, through an exact-byte-checked original assignment
prefix. No server entrypoint, server thread, listener, world or game loop starts.
The expanded context requires its own acceptance; earlier startup evidence does
not qualify it. Native fingerprint and configuration errors remain visible.
Late mixin observation retains both the original pending configuration set and
the active processor's prepared configurations. A native loader may trigger
preparation before the observation seam, consuming entries from the pending set.
Observation does not prepare or requeue configurations; actual transformed-target
witnesses and missing-configuration refusals remain separate requirements.

The selected context includes the pack-pinned Biomes O' Plenty artifact because
saved GT worldgen definitions require its `mountain` and `volcanic_island` biomes.
Original discovery, access-transformer input, SERVER proxy, configuration and
preInit register its complete configured biome set before GT initialization.
`nativeBiomes` records enabled/disabled configuration, original artifact/classloader,
Forge and BOP membership, and existing dictionary tags without lazy tag inference.
`nativeWorldgenBiomeBindings` records actual initialized GT definitions and their
original biome-map function values on the registered biome objects. These are
registration/configuration observations; no world generation runs.

`nativeStoredRecipes` passively observes the current GT recipe-map lookup, including
hidden recipes, with identity-based reference deduplication. Typed stored values
include ordered ingredients and outputs, chance data, properties, NBT and selected
capability state. Category membership is reported separately because it can contain
recipes absent from the lookup. Observation does not invoke matching, ingredient
cache refresh, chance evaluation or builder methods. Unsupported stored values and
callbacks produce explicit affecting gaps; they are never silently omitted.
These records preserve each attempt's actual output order and contents. Native
recycling can vary between unchanged-source attempts: its amount-only ordering
and output limit do not establish a stable choice among equal-amount materials.
Complete value observation does not establish deterministic whole-registry
comparison or prove that every observed difference was caused by a saved edit.
Selected latex recipe properties retain their ordered block states, including
enum, Boolean and integer values. Each state must belong by identity to its
registered block's original container and property definitions; observation does
not query a world or convert block metadata.
Selected jet wingpack capability observations retain the original fieldless flight
provider and the fuel filter's static identity, along with the existing tank fields
and stack NBT. Observation does not invoke flight, fuel lookup or drain callbacks.
For the original GroovyScript `reuse()`, `noReturn()` and `withNbt()` functions, a checked
observation hook retains the actual lambda factory result and captured NBT by
identity. The original factory, setter, return value and predicate remain intact;
reporting describes the captured values without evaluating the function.
The original ICBM bomblet capability factory similarly retains its bound native
stack-copy supplier and captured stack. Capability observations preserve registered
type and storage-key references to the same provider, stored customization lists
and cluster entries. They do not serialize capabilities, call the supplier or
construct a projectile.
OpenComputers battery capability observations retain the captured stack and native
item delegate fields, including its registered parent item, without calling charge
or energy queries. NBT condition records distinguish the original `ANY` constant
by identity as well as retaining its stored fields.

Remaining admission gaps are Workbench context failures, not evidence of invalid
saved source. The original initializer can return at AVAILABLE after Groovy logged
an error and stopped a script stage. That return and complete values for a partial
registry do not qualify recipe execution. `candidateDispatchObservations` describe
attempted call sites, not effective membership. Full required recipe execution,
final effective-state acceptance and the installed recipe workflow remain unfinished;
the checker continues to refuse recipe acceptance.
