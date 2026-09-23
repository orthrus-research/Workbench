# Native early-platform and coremod initialization

The pack material worker now executes the complete original
`FMLInjectionData.build` and `Loader.injectData` before its bounded transformation
and material path. This replaces the pack path's direct assignment of the FML
home field. It establishes native Minecraft/MCP/Forge versions and applies saved
`config/forge_early.cfg` through original Cleanroom configuration code. Missing
settings use native defaults. Native writes affect only the disposable worker
copy; Axiom does not rewrite the developer's configuration.

The worker also implements a bounded original coremod initialization/injection
path when the complete original IVToolkit artifact is supplied. Integrated native
observations using engine03/runtime02 passed all seven coremod conformance cases:
six focused cases and the unchanged saved pack. Every check still returned
incomplete context. This is a **bounded prerequisite**, not completion
of the required-stack initialization vertical or a replacement launcher. The
material-baseline MVP gate remains open. See [initialization MVP](initialization-mvp.md).

## Original owners and actual execution

The [source lock](../sources/material-program.lock.json) binds Cleanroom
0.6.12-alpha, revision `fe78db8dc4fcee47df7549230858f4c064208d73`.
The selected pack is Supersymmetry 0.1.16.15 at
`3e83cd7bad57bd4c424de4e6cc707ab02fe32f54`.

| Owner | Behavior executed | Boundary |
| --- | --- | --- |
| `FMLInjectionData.build` | Native version/home setup and both original configuration-registration calls. | `ModListConfig` is skipped by original SERVER-side configuration logic, not a removed host call. No client is launched. |
| `Loader.injectData` | Receives the original injection array, including its actual shared container-list reference. | This is not `Loader.loadMods`, dependency sorting or construction dispatch. |
| `CleanroomContainer`, `MixinBooterModContainer`, `ConfigAnytimeContainer` | Original constructors, metadata and processed-version methods. | Not the complete built-in roster; not inserted into `Loader.namedMods` or treated as activated. |
| `IvToolkitLoadingPlugin` contract probe | Original constructor and transformer/setup/access/container contract methods, including its native Java compatibility check; original returned container class is instantiated. | The existing `ivToolkit` observation is captured before native injection, not updated into a full lifecycle claim. |
| `CoreModManager.handleLaunch` prefix | A separately named method retains the exact original bytecode from entry through initialization of `loadPlugins`. Native home/tweaker/environment decisions, logging, cascading-tweak queue and patch-transformer registration remain. | Stops before the root-plugin loop. Original `handleLaunch` is not replaced; no full discovery or launcher execution. |
| `CoreModManager.loadCoreModFromDiscoveredJar`, `FMLPluginWrapper.injectIntoClassLoader` | Original IVToolkit registration, annotation/version/certificate checks, native injection callback and container-list append. | Requires the original complete artifact and the bounded unsigned plugin contract. Not complete coremod selection, root setup or ordering. |
| `InjectedModContainer`, `LoadController` | Original returned container is constructed and wrapped, then its original `registerBus` runs through native LOADING/FMLLoadEvent dispatch. | Separate injected-IVToolkit-only activation; host Loader state is restored. Not a loaded-state oracle for material callbacks. |

IVToolkit comes from the original pack-hashed `IvToolkit-1.3.3-1.12.jar`, not a
newly implemented library. Its Java check requires **at least Java 8**, not exactly
Java 8. The selected plugin returns no ASM transformers, setup hook or access
transformer. Its original injection callback is itself empty; Axiom does not
replace it with a no-op. The native wrapper still executes it and appends the
actual returned container class to `FMLInjectionData.containers`. A changed
contract stops the bounded check instead of silently skipping new behavior. Its
original classes remain separately supplied local inputs, not bundled into the
redistributable Axiom engine.

Cleanroom supplies MixinBooter and ConfigAnytime internally. Treating their
standalone core plugins as ordinary mod-entrypoint candidates would miss that
native distinction. Saved `CUSTOM_BUILT_IN_MOD_VERSION` controls whether their
container versions use the saved overrides. ConfigAnytime's original processed
version is unbounded even when its displayed version changes; Axiom preserves
that behavior rather than synthesizing a version object from the display string.

## Exact initialization seam and transformer prerequisites

`NativeCoremodPrefix` admits only the original `CoreModManager.class` with SHA256
`89e20ba1a6592f695a0f7d1c0513cff24ffc07feed09b41acb4bd9d888dd5d30` from the selected
native image/universal artifact. It adds `axiom$initializeCoremodEnvironment` in
the original owner, copying the contiguous original `handleLaunch` prefix through
its unique `loadPlugins` write and adding a return. Original instructions,
branch targets, stack frames and contained exception handlers remain. The
descriptor, native field writes/calls, unique boundary and closed control flow
are checked. Changed bytes or a branch/handler crossing that boundary refuse.
No manager fields are manually assigned to imitate native initialization.

Foundation's original explicit-transformer mechanism installs this seam before
`CoreModManager` is defined, including when its package is excluded from ordinary
transformers. The native server tweaker constructor is used; no client/server
launch target or game loop is invoked. Cascading tweakers are queued by the
original prefix, not reported as fully consumed or executed.

The pack bootstrap retains all seventeen original `FMLLaunchHandler` constructor
transformer exclusions in native order before registering `PatchingTransformer`.
These affect transformation filtering, not classloader ownership. The existing
GroovyScript core exclusion is separate. In particular, the native shaded patcher
package must be excluded before `ClassPatchManager.INSTANCE` is initialized:
transforming its own dependencies during construction can recursively re-enter
the still-uninitialized patch manager. The correction retains original exclusions;
it does not replace patching with an Axiom no-op or remove required input bytes.

The current full-artifact inventory enables IVToolkit injection; the projected
metadata inventory does not. The actual complete artifact hash and executable
plugin/container bytes must match. Unknown manifest behavior or signatures require
the real target class-space/selection path. Original injected wrapping preserves
IVToolkit's native null-source fallback to `minecraft.jar`; that fallback does not
prove the whole launcher class space has been established.

## Result and failure reporting

`result.bootstrap.platformInitialization` reports the native early values,
saved-input/worker-file identities, container classes and versions, and the
pre-injection IVToolkit contract result. Its new `coremodComposition` member
separately reports admitted original injection, the source-bound prefix, queued
tweakers and actual appended/wrapped container, or why that step was not admitted.
Both retain explicit exclusions of full coremod injection and launcher composition.

`execution.addonDiscovery.nativeInjectedCoremodActivation` observes the separate
original IVToolkit-only bus transition. Existing candidate-only mod-presence
observations remain bounded. Neither temporary state is retained as a loaded-mod
oracle for material callbacks; original host Loader references are restored.

An early-platform failure retains a bounded native cause chain and prevents
candidate compilation. Missing required Supercritical Element/OrePrefix
transformations likewise report an incomplete bootstrap before candidate
compilation. Neither case is reported as invalid developer Groovy. Saved-source
custody remains available. Missing source locations are not invented for platform
failures. Existing material errors retain their native saved-script locations.

`tools/axiom_pack_platform_conformance.py` exercises missing/default configuration,
enabled/disabled overrides, an invalid boolean handled by native defaults, a saved
plugin blacklist, fresh-worker reset, native script error and developer correction.
The early-platform suite observes the saved blacklist; complete native loader
selection still needs to consume it in the subsequent composition. The new
injection path refuses admission when the saved blacklist names IVToolkit,
retaining a visible incomplete-context gap and performing no injection or bus
transition. That fail-closed scope guard is not presented as execution of the
original discoverer's blacklist decision.

`tools/axiom_pack_coremod_conformance.py` exercises default/custom configuration,
blacklisted-plugin refusal, fresh-worker reset and native script error/correction
cases for this new path. Those six native cases and the unchanged saved-pack case
passed in the integrated engine03/runtime02 run, after the exclusion correction.
Seven saved-edit/effect cases also passed with this composition. These are passing
bounded observations, not complete launcher, installed-pack, performance or MVP
qualification; all fourteen checks retained their incomplete-context result.

## Remaining work

Compose the actual native required-stack container/coremod selection, relevant
API/version/dependency ordering and original early/late mixin-loader conditions.
Do not call the discoverer against Axiom's tool classpath and claim it is the
target launcher composition. Keep original source/artifact and class-space
identities explicit. Then admit dependent Susy construction and generated
material/item/fluid initialization in their native order.

Root setup is an important remaining boundary: original
`FMLSanityChecker.injectData` initializes binary patches and the deobfuscating
remapper, while the current native Minecraft image is prepared using CLIENT
patches, remapping and access transformation before the SERVER-side worker starts.
Reconcile side, input class space and transformation ownership before extending
root-plugin execution; blindly reapplying patches can fail native input checksums.
The patch manager's original pre-setup passthrough is not root-bootstrap or
patch-stage qualification.

The existing selected mixin subsets and partial material context remain bounded;
they are not newly qualified by early configuration or container construction.
LadyLib/Gaspunk and all-pack embedded-library activation are not prerequisites
unless a supported operation actually depends on them. Exact installed-pack JVM
and completed-scope performance qualification remain open. Resource policy,
Core/profile ownership, no-launch operation and no automatic source repair are
unchanged.
