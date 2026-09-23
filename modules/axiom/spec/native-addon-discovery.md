# Native addon candidates, API providers and candidate bus states

Axiom can bind a complete, profile-selected artifact declaration inventory to its
native material runtime. It uses the original Cleanroom `ASMModParser` to read
every supplied classfile, then reparses original mod-entrypoint bytes inside the
isolated worker. It also runs native API-provider discovery and event-bus
registration for those candidates.
This is **candidate evidence, not complete-pack native mod activation**.

## MVP priority versus inventory breadth

The [initialization MVP](initialization-mvp.md#required-stack-and-scope) targets
GroovyScript, CleanroomMC/Forge infrastructure (including MixinBooter and
IVToolkit), GTCEu, GCYM, Susy-Core, SussyPatches and Supercritical. The broad
selected-artifact inventory below is existing metadata evidence, not a requirement
to initialize every selected mod. Prioritize original platform/container and
transformation behavior needed by that stack, then its registration effects.

An unresolved metadata requirement does not by itself prioritize a mod's entire
lifecycle. In particular, LadyLib belongs to tertiary Gaspunk support, not the
next MVP slice. Preserve its observed metadata and current unresolved state;
admit additional execution only when a supported path actually depends on it.
Do not invent native loaded-state answers, drop required transformations or
treat undiscovered containers as absent. Required native dependency sorting must
receive a faithful composition for the declared scope, not an arbitrarily
filtered list designed to pass. Full all-pack extraction/activation is not a
prerequisite in its own right.

## Input and ownership

`tools/build_axiom_addon_inventory.py` reads every mod descriptor at the selected
pack revision, applies the explicit physical side and optional selections, and
requires matching bytes for every selected artifact. An existing Axiom artifact
bundle can supply those bytes, but its earlier client/server selection is not
reused as authority: hashes and selection are independently rebound to this build.
Missing or changed selected artifacts fail the build; a partial inventory cannot
be used as evidence of absence.

The output is local-only `addon-inventory.jar`, `program.json` and hash-bound
candidate JARs under `native-addon-home/mods/`, retaining each selected descriptor's
original filename. Original
entrypoint classfiles are stored as non-loadable resources, not added as executable
addon classes. Metadata includes artifact/class hashes, native annotations, JAR
manifests, excluded descriptors, the selected revision and side. Raw artifact
inspection does not execute supplied mod classes or their callbacks.

The normal v6 inventory projects original `@Mod` entrypoint bytes, `mcmod.info`,
`version.properties` and each declared mod's original English language resources
in original JAR entry order. Both `en_us.lang` and `en_US.lang` are retained when
present; the native server reader owns fallback and parsing. It also retains
original `@API` declaration classes and one native-eligible original class per
declared API package in every selected JAR. These package witnesses preserve
embedded API copies even when their JAR has no corresponding `@API` annotation.
Original manifest entries are retained byte-for-byte with their entry casing,
continuation lines and named sections; `manifestInputs` binds their sizes and
hashes. The parsed main-attribute map is not a replacement for these resources.
Every retained class is byte-bound; none is fabricated. It does not sort
entries or rewrite metadata/resources. Candidate JARs are read-only inputs, never executable
classpath entries. `--candidate-inputs complete-artifacts` instead retains entire
original JARs for differential verification. Both modes use the same native
reader and preserve native logging; neither writes these JARs inside a worker.

Pass this directory to `tools/build_axiom_material_runtime.py --addon-inventory`.
The runtime builder checks the selected context, native input policy, build recipe,
and correspondence with its executable API artifacts. Core still owns process
management; this does not add a downloader, launcher or provisioning service to
Axiom. A runtime without this optional inventory retains its existing incomplete
pack behavior and reports `not-supplied` for this step.

The inventory describes the **fixed profile/runtime artifact selection**. It does
not certify an installed launcher instance, discover arbitrary workspace JARs,
or incorporate live edits to pack descriptors. Saved Groovy/configuration remains
captured afresh by the existing material-check command.

### Native filename identity and library selection

JAR filenames are semantic inputs to Cleanroom: library enumeration orders them,
and later classpath/coremod filtering compares their names. Content hashes remain
custody evidence, not replacement filenames. Both projected and complete-artifact
inputs use the same original flat `mods/` layout, bound through the runtime manifest.

`addon-library-candidates` executes complete original `LibraryManager.getCandidates`,
including its `gatherLegacyCanidates` call (the selected upstream spelling), JDK
manifest reads, basic mod-list lookup and candidate caching. It reads the immutable profile artifact
directory and supplies the order used by subsequent original JAR discovery. This
replaces descriptor order; Axiom does not implement a parallel sorting algorithm.
`nativeLibraryCandidates` records the exact original filename order and scope.
Missing, duplicate or unbound enumerated artifacts fail the check.

This call uses explicitly empty launcher additions and a fresh native mod-list
cache with no external lists. Unbound overrides and a previously consumed candidate
cache are rejected. Native manifest admission verifies raw bytes and original JDK
parsing before the selection call; a Bansoukou declaration requires an executable
closure that this context does not supply and is rejected before classpath mutation.
The current selected artifacts have no Bansoukou declaration. Native cache reuse
is checked by identity. Original home/cache/launcher-argument references are
restored afterward, with mod-list state and executable URLs checked unchanged.

`manifestSelection` reports raw-manifest custody and embedded-dependency carriers.
The selected JARs contain five such carriers, including Gaspunk's Ladylib input.
Retaining their manifest declarations does **not** extract or activate those
dependencies. `LibraryManager.setup`, external repository contents, nested dependency
extraction, Bansoukou execution, classpath candidates and coremod filtering remain
uncomposed. The complete method is executed on these explicitly bounded inputs;
this is not complete target-launcher selection or `CleanroomModDiscoverer.identifyMods`.
No files are copied or renamed in the worker or the developer's checkout.

## Native observation

`execution.addonDiscovery` and the `addon-candidate-discovery` trace step report
candidate identities for GTFO's `nuclearcraft`, `actuallyadditions`, `applecore`
and `gcys` queries, plus GTFO's own annotation and ordering declaration.

Before inspecting candidates, the pack worker executes the complete original
`FMLServerHandler` constructor → `FMLCommonHandler.beginLoading` →
`MinecraftForge.initialize` chain. This establishes native SERVER-sided state
without calling `beginServerLoading`, `finishServerLoading`, `Loader.loadMods`
or starting a Minecraft server. The existing native vanilla registration prefix
already supplies the original blocks and items needed by Forge's tool setup.
The call runs before assigning the GregTech owner; it is not a GT callback.

No individual initialization call is skipped: native ore-dictionary initialization,
worker-local username-cache loading, fluid-registry validation, tool initialization
and crash-report class setup all execute. No player cache is imported from an
installed game. `execution.forgeInitialization` records the returned native side,
unstarted handler, absent active owner, tool state and representative harvest
levels. The `forge-platform-initialization` trace step reports its actual duration
or failure, not a simulated pass.

Axiom constructs original `FMLModContainer` metadata objects for all raw `@Mod`
declarations without constructing their mod instances. It invokes original
`shouldLoadInEnvironment` against that native sided state. The compact
`nativeSideEligibility` observation includes counts and byte-bound restricted
declarations. This is still **raw annotation evaluation**, not the filtered
Cleanroom discovery result. For example, the selected SoundPhysics JAR declares
separate client-only and server-only classes; the native method rejects the first
and accepts the second. A duplicated raw mod ID is not automatically a conflict.

Before applying saved enabled states, the worker invokes original
`Loader.readInjectedDependencies`, then complete `ModCandidate.explore` →
`JarDiscoverer` → `ModContainerFactory` → `FMLModContainer.bindMetadata` for each
selected JAR. Native entry filtering excludes terminal-`$` Scala companion
classes; native side checks exclude ineligible declarations. Original metadata
fallback, version properties, Minecraft ranges and dependency parsing remain
intact. The selected inputs yield 162 containers, distinct from 194 raw annotations
and 170 raw side-eligible declarations. Discovery does not construct mod instances.

Saved `config/injectedDependencies.json` contributes original before/after
dependencies. Missing and empty files, duplicates, malformed input and a native
partially read list retain the loader's actual behavior. In particular, its caught
parse error does not roll back earlier entries. Axiom preserves that error and
partial state; it does not repair the file or assign a fabricated Groovy location.
Each check starts a fresh worker so a later file deletion restores native defaults.
Dependency injection is not injected mod-container discovery.

`nativeCandidateDiscovery` records native candidate order, the complete metadata
digest, input scope/size, class-entry exclusions and saved dependency identity.
`gtfoNativeCandidate` and each available query's `nativeCandidate` expose metadata.
Discovery shares an original `ASMDataTable` across these JARs. The projected
table is not a complete subscriber table. Projection/full
comparison establishes these candidate observations only, not arbitrary ASM data,
complete activation, constructor execution or dependency sorting.

Before saved disable settings or candidate-bus registration, `addon-api-providers`
executes the complete original `ModAPIManager.registerDataTableAndParseAPI`.
Native code owns API versions, owners, self-reference handling and dependency
edges, including package copies embedded in other mods. `nativeApiProviders`
records those outputs and the original candidate-package membership, binding
provider sources to original artifact hashes rather than projection hashes.

The selected inputs produce 79 API providers. Native case and side behavior
matter: ComputerCraft's API owner is `ComputerCraft`, while its two discovered
mod IDs are `cctweaked` and `computercraft`; OpenComputers also embeds its API.
CTM's client-only mod is rejected on SERVER, but its API annotation remains
discoverable and GregicProbe embeds that package. An empty contained-mod list
therefore does not mean the JAR contributes no API. These are native observations,
not normalized or inferred loaded-mod answers.

The API manager's prior `dataTable` and `apiContainers` references are restored
by identity. No candidate API transformer is installed and API containers are
not added to the candidate-bus or dependency-sort inputs. This witnesses APIs
within the selected JAR set, not complete Cleanroom API composition or activation.

For actual discovered containers, the worker applies the original
`Loader.disableRequestedMods` method to the saved
`config/fmlModState.properties`, observes each container's configured `enabled`
field, then restores the standalone loader's original map and state-file pointer.
Native Java properties parsing and `Boolean.parseBoolean` own these decisions.
No host implementation of the configuration semantics is substituted. Unbound
launcher `fml.modStates` system-property overrides are not accepted.

The `addon-candidate-bus` hook then creates an original `LoadController`, invokes
its original transition to `LOADING`, and dispatches `FMLLoadEvent`. Its native
`buildModList` invokes every candidate's original `registerBus`: enabled
containers become `LOADED`, disabled containers become `DISABLED`. No mod instance
is constructed. Original `FMLServerHandler.addModAsResource` and `LanguageMap.inject`
also execute, including original language parsing and fallback. Axiom records
the resulting language entry count and deterministic digest, not replacement
translations. Native language side effects remain in this fresh worker.

`nativeCandidateActivation` records the exact candidate states, active candidate
order and original `Loader.isModLoaded` answers **only for witnessed candidate
IDs**. The candidate order is registration order, not dependency-sorted order.
The temporary loader's `mods`, `namedMods` and `modController` references are
restored by identity afterward. This is not a promise to reset all native static
state, nor a complete-pack loaded-mod oracle for later callbacks. The public
GTFO query states remain `unresolved`.

Dependency ordering is explicitly deferred. Required IDs outside the candidate
set are reported as unresolved composition inputs, **not missing-mod errors**;
native version requirements are not yet checked. The selected baseline reports
`cleanroom`, `forge`, `ivtoolkit`, `ladylib`, `mixinbooter` and `tickcentral` outside
this candidate set. Built-in/injected containers and API providers must be
composed before calling original `Loader.sortModList`/`ModSorter` faithfully.

These facts must remain distinct:

| Fact | Meaning |
| --- | --- |
| `declarationStatus: declared` | Original selected bytes contain this `@Mod` identity. |
| `no-Mod-annotation-in-selected-artifacts` | Complete selected artifacts have no such annotation; this is **not** a loaded-mod absence claim. |
| `configuredEnabled` | Original loader configuration left this metadata container enabled or disabled. |
| `nativeSideEligible` | Original container metadata method permits this declaration on the native SERVER side; it does not prove activation. |
| `nativeLibraryCandidates` | Original manifest-aware library selection on fixed flat inputs, not complete Cleanroom composition. |
| `nativeCandidate` | Original JAR discovery returned this metadata container; not proof of activation. |
| `nativeApiProviders` | Original API-provider parsing and package membership within the selected JAR set; not full loader composition. |
| `containerStates: LOADED/DISABLED` | Original candidate-bus registration outcome in the explicitly incomplete JAR-candidate composition. |
| `loadedState: unresolved` | Cleanroom-wide selection, injected containers and activation have not been established. |

Writing `gcys=true` into the properties file cannot create a mod declaration.
Likewise, GTFO's `after:gcy_science` ordering string does not answer its separate
`Loader.isModLoaded("gcys")` query. Query answers are never installed into the
running material host from this inventory. GTFO construction/material callbacks
remain deferred, and the overall pack result remains incomplete.

## Remaining native dependency

The pinned `Loader.loadMods` uses `CleanroomModDiscoverer`, not just the older
`JarDiscoverer`. Discovery includes classpath/library candidates, built-in and
injected containers, and coremod/mixin filtering. `Loader.isModLoaded` also
consults the `LoadController`'s disabled state. Annotation/configuration evidence
alone cannot reproduce those operations.

The sided initialization, original-filename/manifest flat-input library selection, original
JAR metadata, JAR-scoped API-provider parsing
and candidate-bus prerequisites now execute.
Cleanroom classpath/library selection, injected-container/coremod handling,
complete-composition `LoadController` state and API containers, dependency
sorting and construction ordering remain open. Compose those required by the
MVP stack first. GTFO callback admission still requires its native conditions
and dependencies; the current deferral is not waived, but is not automatically
the next product milestone. The inventory does not supply fabricated
`Loader.isModLoaded` answers.

## Verification and reporting

`tools/axiom_pack_candidate_metadata_conformance.py` checks native metadata and
saved dependency edits, duplicates, malformed input, partial native error state,
file removal/fresh restoration and the unchanged saved pack. Supply
`--reference-runtime-home` with a complete-artifact runtime to compare the same
cases serially; an original-filename/order/raw-manifest/metadata, API-provider/package-membership,
candidate-bus or language-map
disagreement fails the comparison. Projection sizes are setup/data
sizes, not a complete initialization performance claim. Source locks include
native filtering, metadata and version/dependency owners at immutable revisions.

`tools/axiom_pack_addon_discovery_conformance.py` exercises complete programs with
missing, enabled, disabled, invalid and repeated native mod-state settings, plus
unknown IDs, separately disabled AppleCore/GTFO, both disabled and fresh restoration.
It also accepts `--reference-runtime-home` for the same full-artifact comparison.
`--whole-pack` preserves the complete
selected saved checkout. It verifies original Forge state and native side decisions,
including the distinct SoundPhysics entrypoints. An unchanged material phase, native saved-edit errors
and source highlighting must continue working; no new expectations file is needed.

Immutable scanning is setup work and can be reused with its exact input identity.
Mutable configuration checks execute in fresh workers. Existing CPU, wall-time
and JSON limits are unchanged. MVP development temporarily uses a 2 GiB worker
heap while completed-scope memory requirements are established. The trait
catalog retains immutable method signatures, not full compiler instruction trees;
this does not change emitted bytecode or strip source/debug information.
Language-map observation streams the same sorted Gson JSON into its UTF-8 digest
without allocating a second full JSON string and byte array. Native language
entries and ASM data are retained, not trimmed to meet the resource policy.
Discovery metadata does not establish full-pack
initialization, installed-JVM qualification or the end-to-end performance target.
