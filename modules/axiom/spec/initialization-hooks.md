# Native initialization stages and hooks

Axiom executes a bounded native initialization path without launching the
Minecraft client, opening a world, rendering, or starting a language server.
It retains complete saved Groovy/config input; lightweight execution must not
mean deleting inconvenient pack sources or substituting addon rules.

## Observed execution

`execution.initialization` is a passive trace of explicit host stages and the
native hooks actually invoked. It records the owner, actual visit sequence,
status and monotonic elapsed nanoseconds. It does not schedule callbacks or
claim a complete per-listener trace of Forge's event bus.

| Status | Meaning |
| --- | --- |
| `returned` | Control returned to the host. Native logged errors may still exist. |
| `threw` | The observed operation threw; its exception type is recorded and the original diagnostic is retained separately. |
| `not-reached` | The step was planned but never entered; no execution timing is invented. |
| `deferred` | This context intentionally does not execute the work; its reason is explicit. |

Configuration setup and native configuration registration are separate steps:
an absent saved file fails setup before `SusConfig` initialization is entered.
An early host failure also retains the trace accumulated before that failure.
Nested timings overlap; do not sum them into a full-run benchmark. These records
are available through the installed command's native result, not a new IDE panel.
Timings are diagnostic metadata, not material semantic identity.

`result.workerStages` adds outer runtime-verification, source-intake/admission,
native-bootstrap and material-context timings. They do not include all CLI/JVM
startup costs. The nested `gregtech-configuration` stage runs Cleanroom's native
annotation parser and configuration load/sync against the complete original GT
ConfigHolder before catalog initialization. See [native mutations](native-material-mutations.md)
for the catalog/mixin dependencies and the next custom-item boundary.

The pack bootstrap now runs complete original `FMLInjectionData.build` and
`Loader.injectData`, including native early configuration, before the bounded
transformation path. Its [platform observation](native-platform-initialization.md)
separates actual constructors/plugin-contract calls from still-unexecuted loader
activation. A returned outer bootstrap step can contain a retained platform
failure and `admitted=false`; inspect the nested outcome, not just the return.

The pack context executes the original GTCEu
`GroovyScriptModule.onCompatLoaded` callback, including all five object mapper
registrations (`element`, `material`, `metaitem`, `oreprefix`, `recipemap`) and its
material expansions. The registry is inspected after the callback returns.
The earlier conflict-only `ObjectMapperManager` projection has been removed;
the manager now comes unchanged from the selected native GroovyScript artifact.
That artifact also supplies the bundled Eclipse completion data classes needed
to link the callback. Availability does not start a language server or qualify
every mapper/expansion for developer source execution.

Native `.acidic()` remains available when the Susy-specific `recipeInfo` option
is false. Native `.basic()` does not. Invalid extension arguments are resolved
by original Groovy dispatch, not by a replacement argument validator.

## Source-backed composition map

This map uses selected Supersymmetry 0.1.16.15, GTCEu 2.8.10-beta, Susy-Core
0.1.118, GroovyScript 1.4.3 and Cleanroom 0.6.12-alpha. Exact primary revisions
are in the [source inventory](../sources/material-program.lock.json). GCYM
1.2.11, Supercritical 0.2.7 and GTFO 1.12.10 callback bodies and annotations were
also inspected in the exact pack-hashed native artifacts. This is not a claim
about the latest upstream versions or a completed native addon composition.

| Stage/hook | Original behavior that must be preserved | Axiom boundary |
| --- | --- | --- |
| Saved configuration | `SusConfig` invokes ConfigAnytime and native Cleanroom synchronization. | SussyPatches path observed; whole-pack configuration incomplete. |
| GT Groovy compatibility | Original callback registers mappers and material expansions, including `.acidic()`. | Callback executes; complete plugin discovery/blacklist handling remains pending. |
| Susy Groovy compatibility | Original callback conditionally installs Susy expansions and `.basic()`. | Callback executes with saved `recipeInfo`; not complete addon loading. |
| Native mapper bindings | After compatibility registration, GroovyScript binds registered original mappers before PRE_INIT. | Native sandbox registration runs for the five present GT mappers; only material is admitted. General mapper initialization/plugin discovery remains incomplete. |
| Groovy `PRE_INIT` | Load complete configured sources and register their listeners before material producers. | Native loader executes; cross-loader availability does not execute `POST_INIT`. |
| GT material section | Marker registry → registry event → open registries → GT catalog → material event → close → post-material event → freeze. | Host checkpoints and event posts observed; required addon listeners are not all installed. |
| Susy `CommonProxy.registerMaterials` | HIGH-priority listener calls six complete producers, then property changes. | Original complete subscriber registered through native EventBus; callback effects observed. Full addon discovery/order remains unqualified. |
| GCYM `registerMaterials` | HIGH-priority callback normally runs GCYM producers; Susy's selected GCYM mixin cancels it at entry. | Complete original subscriber registered after observing the original mixin; cancellation witnessed through the unchanged catalog and complete programs. |
| Supercritical configuration and registry | Original annotation-based configuration precedes NORMAL registry creation during PRE. | Native config and separate SC registry execute when the saved/native material-modification setting already matches Susy's false override. Other settings defer composition. See [SC events](native-supercritical-material-events.md). |
| Supercritical material callback | HIGH-priority callback registers materials and ore prefixes. `SCMaterials.register` creates Corium before testing `disableAllMaterials`. | Complete callback and required original ore-prefix mixin execute in the composed context. Both catalog branches observed; damage callbacks remain unexecuted. |
| GTFO configuration | Original annotation-based native configuration precedes construction overrides. | Native load/sync executes with saved values and original defaults; construction remains deferred. See [GTFO prerequisites](native-gtfo-prerequisites.md). |
| GTFO material callback | NORMAL-priority callback initializes its material handler, whose static initializer creates material/shaped-item declarations; an additional branch queries `gcys` presence. | Pending verified mod-presence state and complete callback composition. `SHAPED_ITEM` is created by its own original class initializer, not by calling later food/tool initialization early. |
| Susy post-material callback | HIGH priority: add twelve prefixes, modify flags and initialize generated-fluid handling. | Original callback executes in focused programs and the unchanged pack, including pipe properties and molten queues. |
| GCYM post-material callback | NORMAL priority: alloy properties, late flags and generated fluids; Susy's mixin forces molten generation for six named materials before the fluid handler. | Original callback and mixin execute; alloy/queue effects and native failure witnessed. Fluids remain unregistered. See [GCYM events](native-gcym-material-events.md). |
| Supercritical post-material callbacks | NORMAL priority: proxy adds nine prefixes; event handler conditionally modifies materials. Susy's construction callback disables that option. | Original callbacks execute when the native setting is already false. A true value explicitly defers SC subscribers; the uncomposed construction override is never fabricated. |
| Pack listeners | `MaterialChanges.groovy` uses LOWEST priority and admits Fiber as a base property; `RegisterMetaItems.groovy` creates custom items in PostMaterialEvent. | Unchanged pack returns from all eleven material producers, changeFormulas, complete ChangeFlags and RegisterMetaItems: 409 custom variants, then material phase FROZEN. Pack-wide generated registry content remains deferred. See [custom items](native-custom-items.md). |
| Later processing | Native generated content, fluid registration, prefix processing and `POST_INIT` recipe bodies have distinct dependencies. | Existing bounded generated-content lane retained; remaining queues and recipe phases explicitly deferred. |

Ordering within the same event priority is not established by the table's row
order. Cleanroom's `ListenerList` appends listeners within each priority and
preserves that order when building its dispatch cache. Full addon discovery and
registration order must therefore be captured, not guessed or alphabetized.
Likewise, the observed GT-before-Susy compatibility call order is the explicit
bounded host composition, not proof of full native plugin discovery equivalence.

## Primary implementation anchors

- GTCEu: `core/CoreModule.java` material section and
  `integration/groovy/GroovyScriptModule.java#onCompatLoaded`.
- GroovyScript: `compat/mods/ModSupport.java`,
  `mapper/ObjectMapperManager.java` and `mapper/Completer.java`.
- Susy-Core: `common/CommonProxy.java`, `common/materials/SusyMaterials.java`,
  `Supersymmetry.java#onModConstruction`, `mixins/SuSyLateMixinLoader.java`,
  `mixins/gcym/GCYMEventHandlersMixin.java` and `mixins.susy.gcym.json`.
  The late loader selects GCYM's mixin configuration according to native mod
  presence; listing the mixin source alone is not observing it applied.
- Cleanroom: `eventhandler/SubscribeEvent.java` (default NORMAL priority) and
  `eventhandler/ListenerList.java` (priority and registration ordering).
- Pack: `groovy/preInit/MaterialChanges.groovy`, `RegisterMetaItems.groovy` and
  `ModifyRecycling.groovy`. Registering a recycling listener is not running its
  later recycling event.

## Current acceptance boundary

The native regression lane covers config conditions, callback registration,
`.basic()`/`.acidic()` dispatch, invalid arguments, source locations, stopped and
deferred trace states, fresh workers and the unchanged whole-pack capture.
The installed command has the same bounded evidence. Full pack initialization,
per-listener ordering, property/form coverage, recipe effects and exact installed
JRE identity remain unqualified. No full initialization latency claim is made.

[Native addon property witnesses](native-material-properties.md) additionally
exercise original Fiber, MillBall, DummyABS and Coolant behavior, including
partially applied state after verification errors. They do not mark any deferred
addon event callback as executed or establish same-priority listener order.

The former missing-material stop identified this composition dependency:
`CommonProxy.registerMaterials` (HIGH) calls `SusyMaterials.init`, which invokes
six complete producers and then `changeProperties`. Its
`SuSyFirstDegreeMaterials.init` creates `MolybdenumDisilicide` with native ID 8792.
The pack's LOWEST material listener later reads that original static field.
The original callback now supplies this field; no declaration is copied into a
replacement catalog. All six original producers remain intact, including the
upstream empty Element/Organic methods, followed by complete `changeProperties`.

## Original Susy subscriber composition

`execution.susySubscriber` records seven native handlers registered from the
complete original `CommonProxy` after native side transformation. EventBus performs
its original reflected-method/type resolution and creates its own handlers.
Registration is checked against the actual listener list because native registration
can log and swallow individual failures. The host supplies explicit `susy` owner
metadata and restores the previous active owner; this is not full FML discovery.

`materialEventDispatch` and `postMaterialEventDispatch` snapshot the native dispatch
cache immediately before each post. Indices are the actual list order, not sorted
callback names. They are not evidence that every listed callback was invoked or
returned. Focused programs observe Susy's HIGH handlers before their LOWEST/NORMAL
script listeners. Ordering relative to absent addons remains unqualified.
MaterialEvent does not implement IContextSetter: registrant owner, active owner,
resource namespace and storage registry must remain distinct.

Registering the complete subscriber also installs block/item/recipe handlers.
The pack context therefore explicitly defers generated content instead of posting
registry events without the required addon setup. It reports NOT_STARTED content,
no generated-content vocabulary, and a context gap. The GT-base content lane is
unchanged. Material-phase completion never implies those deferred stages passed.

`tools/axiom_pack_lifecycle_conformance.py` observes catalog reads, native edits and
errors, property changes, post-material effects, and fresh-worker isolation. Susy's
two original `kreep_basalt` declarations remain distinct static objects (IDs 27208
and 27209); the native named registry resolves ID 27209. Axiom neither repairs nor
deduplicates them. These are bounded native observations, not a whole-pack oracle.

The [native material mapper](native-material-mappers.md) now uses actual sandbox
binding identity and original Groovy/native lookup, defaults, logging and errors.
It resolves all four mapper call sites in the unchanged material producers. This
does not admit item/fluid/recipe mappers or execute POST_INIT recipe scripts.
