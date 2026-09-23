# Conformance and implementation gates

Status: bounded recipe/domain tests, input-package tests, and native runtime
admission. Whole-pack qualification remains false.

The [initialization-check MVP](../spec/initialization-mvp.md) sets current delivery
priority. Its acceptance requires real saved-source/configuration intake, native
initialization feedback and IDE highlighting. The broader machine/circuit matrix
below remains long-term coverage work, not a prerequisite for that MVP. Test
scenarios in which source is repaired model a developer edit; Axiom never repairs
the source itself.

The existing IntelliJ `MaterialChecksActionTest` accepts an optional
`prepareEngineArchive` field in its native fixture. That lane runs the documented
terminal `setup --prepare --engine-archive` command with Core's saved Java selection
before using the registered Saved Checks action. Omit engine/runtime/Java paths
from the fixture's ordinary options; the action must produce no input prompts.
The test preserves existing Core history and identifies only its new attempts.
Terminal setup inherits its configuration environment; ordinary IDE calls keep
their existing transport. Both this lane and the earlier explicit-path lane
retain native error/correction, exact source navigation and history assertions.

## Current executable evidence

- `tools/axiom_material_block_conformance.py` qualifies composed material-prefix
  items, compressed blocks, frames and native ItemBlocks, including sparse
  metadata, native registration failures, namespace collisions and original source
  edits. `tools/axiom_material_item_conformance.py` retains the prefix-only
  regression gate. See [material blocks](../spec/material-blocks.md).

- `tools/axiom_catalog_conformance.py` executes complete GT material registration
  through the native FROZEN checkpoint, with original configuration parsing,
  transformed native material events, live catalog references and all four reached
  configuration combinations. It retains deferred block requests without generating
  content. See [catalog qualification](../spec/material-catalog.md).

- `tools/axiom_native_material_conformance.py` runs complete GT element and Susy
  unknown-composition producers in one native Cleanroom material/fluid context,
  with explicit native owner/listener fixtures. It verifies source-rebuilt program
  equality, registration events, WATER/LAVA reuse, delegate rebinding, listener
  failure effects and an upstream producer edit. See [native material context](../spec/native-material-context.md).

- Java unit tests retain recipe construction, ordinary MIXER selection/start,
  inventory allocation, circuits, failure boundaries and source/package custody.
- Native runtime tests reject altered files, symlinks, unsafe paths and runtime
  injection options. A trusted Groovy fixture exercises real compilation, helper
  calls and JVM execution without Minecraft. This is not a public arbitrary-code
  endpoint or a pack-registration test.
- Python integration tests exercise the Core process port and exact installed
  engine inventory, without implementing recipe algorithms.
- `python3 tools/build_axiom.py --provision` selects the profile-pinned JDK,
  cleans stale build output, runs Java tests and an outside-checkout smoke.
  `tools/axiom_smoke.py` also accepts an explicit `--workbench-python` for an
  already installed Core/API/Axiom closure.
- `tools/axiom_source_conformance.py` binds the selected runtime, engine and
  original GTCEu source, then compiles and directly executes source methods on
  that JVM. It compares 20,320 overclock, 40,000 item/fluid allocation and 20,000
  builder-field validation vectors, including original diagnostic text/order.
  Annotation and domain-type substitutions remain explicit. Shared predicates
  do not independently qualify all upstream item/NBT/ore behavior.
- The same source comparison also checks the retained material extraction and
  compares 48,000 operations over 2,000 native dust/gem/ingot property graphs.
  Directed Java tests cover mutation guards, constructor/setter differences,
  cross-material propagation, deferred validation and effects before failure.
  Name/phase carriers and the restricted property catalog remain explicit
  substitutions; see [material properties](../spec/material-properties.md).
- Source-target, loader-order, composition, artifact and platform inspection
  tests remain. Their acceptance does not mean recipe producers were executed.
- `tools/axiom_registry_conformance.py` compares 24,000 material-registry and
  48,000 fluid-queue operations against independently compiled locked source.
  The original Minecraft registry utilities execute natively without game
  startup. Shared carriers and explicit fluid builder ports remain declared
  substitutions; these checks do not execute actual FluidBuilder or producers.
  See [native registries](../spec/native-registries.md) for inputs and limits.
- `NativeFluidTest` exercises material/fluid construction, identity, native
  scalar defaults, queue/event order, reuse, collision and partial failure,
  including fresh production sandbox workers. `tools/axiom_fluid_conformance.py`
  compares 4096 original FluidBuilder scenarios and 4096 Forge scalar scenarios,
  and executes all ten declarations in the pinned Susy-Core initializer.
  These are isolated producer tests, not a pack bootstrap or recipe registry.
  See [native fluids](../spec/native-fluids.md) for shared ports and limits.
- Native event tests execute the selected Cleanroom dispatcher, native listener
  factories and bytecode transforms. The independent original-source comparison
  covers directed cases and 8,000 seeded cache mutations. GT material lifecycle
  tests retain event priorities, OPEN/CLOSED/FROZEN boundaries and effects before
  failures, with explicit synthetic producers. These are bootstrap prerequisites,
  not a recovered pack registry; see [native events](../spec/native-events.md).
- Worker isolation probes compile controlled Groovy helpers and an AST transform
  inside the actual production mount/kernel policy. They test host-file and input
  protection, network/process denial, thread synchronization, heap limits and
  independent worker state. They are not game or pack-registration tests.

The former custom-JVM comparison suites were retired with their implementation.
Old receipts are historical evidence for that retired backend and cannot
qualify the native execution direction. See [runtime transition](../spec/native-runtime.md).

`fixtures/Coolants.groovy` retains the exact locked source bytes; its supplied
test registry remains synthetic. Receipt totals describe specific runs, not a
percentage of parity completion.

## Qualification principle

Use original upstream methods directly where independently executable; otherwise
review and test an explicit source-to-extracted-rule mapping and every dependency
substitution. Do not launch Minecraft, construct a fake world or use Workbench's
runtime harness as the parity mechanism.

Tests establish evidence for their covered scope. Universal exactness additionally
requires complete relevant source/dependency coverage; hand-authored expected
results must not be the only oracle for a second reimplementation.

Retain source commits, file/method digests, input data identity, coverage, expected
decisions/effects and any assumptions with each conformance case. Independently
check numeric and state-transition edge cases. An upstream surprise is not
silently fixed in the expected result.

## Required cases

| Surface | Minimum cases |
| --- | --- |
| Construction | Actual loader groups; helper loops; meta-class map mutation; material bootstrap; registration/removal order; build callbacks; failed or dynamic construction |
| Matching | Greedy overlapping predicates; original order; split amounts; unused inputs; repeated/nonconsumable requirements; empty-domain preconditions |
| Identity | Typed and missing NBT; item capabilities; wildcard construction; ore expansion/epoch; custom matcher coverage |
| Circuits | Absent vs 0; 32 and invalid settings; physical plus ghost; per-bus visibility; duplicate entries; map limits; lookup vs predicate differences |
| Selection | Fresh tree lookup vs cached recipe; distinct buses/shared fluids; map changes; dynamic resolvers; machine-specific search |
| Preparation | Parallel and overclock arithmetic; output trimming/chances/locks/voiding; route-specific fluid fit; energy buffer; auxiliary resources |
| Special machines | Positional atmosphere/biome; catalyst state and actual arithmetic; ball-mill occupied slots and pre-failure damage; strand/custom processors |
| Time/effects | Progress, interruption and completion; ordered external events; explicit randomness; failed preparation effects; discarded products |
| Isolation | No cross-run registry/cache leakage; read-only original inputs; cancellation/limits; unavailable dependencies fail closed |
| Integration | Standalone operation without Core; installed module resource resolution; exact source identity in results; disable/remove without damaging Core or user data |

Pack examples and implementation references are linked in
[recipe construction](../spec/recipe-construction.md) and
[behavior and parity](../spec/behavior-and-parity.md).

## First implementation acceptance

1. Select native JVM build/JDK/dependency metadata and establish an independently
   runnable library/test setup without game initialization.
2. Verify source references and inventory reachable producer/machine families,
   active dependencies and injections. Keep the unresolved pack index and
   source/artifact equivalence separate from source-analysis readiness.
3. Implement the shared typed values, handler topology, matching and circuit
   foundation from pinned source with explicit conformance evidence.
4. Close one real recipe-construction and machine path end to end, including
   callbacks, selection, context, preparation and explanations. MIXER is a useful
   initial case, not a proxy for every machine.
5. Expose only the qualified scope. Keep every uncovered family visible, then
   extend by complete shared or specialized behavior families.
6. Integrate the real engine with Workbench packaging, native release inventory
   and installed lifecycle tests. Do not register fake capabilities to precede
   that implementation.

Completing a first family is not completion of the all-machines goal. Full target
coverage requires a disposition for every registered processor/producer and
closed dependencies for each positive support claim.

## Baseline and specification checks

This stage checks immutable source/blob/digest references, relative document
links, metadata consistency and Workbench public-tree/package/release policies.
Those checks are not Java builds or machine-parity tests.

A source refresh must update affected references and conformance expectations
deliberately. A floating upstream branch or stale binary tag cannot preserve an
old qualification automatically.

## Platform input verification

`PlatformBundleTest` covers original metadata ordering, native/classpath separation,
explicit selection, policy hashes, stale identities and archive custody. Binary
tests retain exact annotation defaults without applying them to unresolved usages.
`tools/axiom_platform_smoke.py` accepts explicit original platform bytes and checks
independent ZIP/javap evidence outside checkout, optionally through installed Core.
It neither initializes a game nor qualifies loader/registry execution.

## Native constructor identities

`tools/axiom_native_identity_conformance.py` compares original-source and release
Cleanroom transformations, then exercises actual blocks, enchantments and WATER/LAVA
in fresh restricted JVMs. It checks exact prefix boundaries, external input
tampering, native aliases/state IDs, fluid stacks and five GT tool-property
expressions. See [scope and reproduction](../spec/native-identities.md). Generated
Minecraft-derived images are external inputs, never engine artifacts.

## Native items and ore unification

`tools/axiom_item_conformance.py` reconstructs complete retained GT item/unifier
sources, exercises real native items/stacks/capabilities/ore events, and compares
the release's ore behavior with complete original-source Cleanroom classes in
independent fresh JVMs. Native configuration and source-edit witnesses, partial
failure and six explicit crafting-rewrite fixtures are covered. See
[scope and reproduction](../spec/native-items.md). Generated GT content and full
recipe registration remain unqualified; these tests do not launch Minecraft.

## Early native bootstrap qualification

The existing root-stage tool accepts `--stage early` for the separately bounded
original launcher loop described in [the root class-space specification](../spec/native-root-class-space.md).
Its default `--native-context root-only` retains the empty-home early check.
Adding `--native-context supersymmetry:required-early` requires `--addon-inventory`,
`--pack` and `--program` alongside the existing root-stage inputs. The inventory
must retain complete original artifacts; the selected pack Git objects bind the
fifteen required JARs for CodeChickenLib, GeckoLib, GCYM, GTCEu, GroovyScript,
Immersive Railroading, IVToolkit, ModularUI, OpenComputers, Scalar, Supercritical,
SussyPatches, Susy-Core, TrackAPI and Universal Mod Core. The program ZIP carries every saved `groovy/` and
`config/` file unchanged into the fresh native home, with independent digest/count
acknowledgement. Original mod artifacts are linked from verified read-only inputs
under original filenames; each worker retains a separate mutable home. Cleanroom
supplies integrated MixinBooter and ConfigAnytime.

Required-context checks bind native callback order, the complete selected
transformer queue and wrappers, injected container names, and the original
Groovy `CachedClass$1` field-hook transformation. That witness requests class
definition without initialization. Both contexts stop at `INIT`; GroovyScript's
`DEFAULT` mixin application, late selection, construction and material/recipe
execution remain outside this boundary. The default root-stage command retains
its existing behavior.

The required-context positive native run and the affecting configuration/input
conformance cases now execute through the existing supervised probe. Their scope
ends at early initialization; material initialization remains explicitly incomplete.

`NativeEarlyExecutionTests` in `validation/tests/test_axiom_native_early_stage.py`
uses a previously checked required-context candidate, its matching engine and the
selected JVM. Supply all four paths explicitly, with a new report directory:

```sh
AXIOM_EARLY_NATIVE_CANDIDATE=/absolute/path/to/checked-candidate \
AXIOM_EARLY_ENGINE_HOME=/absolute/path/to/engine \
AXIOM_EARLY_JAVA_HOME=/absolute/path/to/selected-jdk \
AXIOM_EARLY_REPORT_ROOT=/absolute/path/to/new-report \
python3 -m unittest -v validation.tests.test_axiom_native_early_stage.NativeEarlyExecutionTests
```

Without these inputs the native class is skipped; partial input selection fails.
It checks the candidate's current source, artifact, engine and JVM bindings and
retains a separate package, command, stdout and stderr for each case. Every launch
uses `NativeRootStageProbe` through its existing supervisor and fixed worker limits.
The original candidate remains unchanged. Complete saved-program variants add
only an ordinary GroovyScript configuration or an output-directory collision.

Cases cover the unchanged baseline, wrong configuration type, malformed JSON,
native removal of a required transformed method, a logged native write error,
missing/changed original SERVER artifact, wrong side/stage, and saved correction
after native failure. Original `SideOnlyConfig` callbacks produce the configuration
outcomes. The malformed-JSON case retains the original early error-reporting
linkage failure for `ICommand`; it does not relabel that gap as a script verdict.
A logged write error prevents readiness even when callbacks and definitions finish.

`NativeEarlyLaunchPrefixTest` includes an optional exact-artifact case selected
with `AXIOM_EARLY_FOUNDATION_JAR`; it reads and defines the extracted class without
invoking native initialization. Supplying `AXIOM_EARLY_CLEANROOM_JAR` and
`AXIOM_EARLY_CLEANMIX_JAR` as well enables the original-bytecode wrapper/priority
and Mixin proxy assertions. These assertions bind exact artifact hashes and
inspect bytes without initializing native transformers.
`NativePackEarlyArtifactTest` uses `AXIOM_EARLY_ADDON_HOME`, pointing to the
inventory's `native-addon-home/` directory, to check the fifteen original JARs,
manifest declarations, coremod priorities and Groovy transformation bytecode.
These optional artifact checks do not execute native callbacks; report skips
when their inputs are absent.
`NativeLoaderPrefixTest` uses `AXIOM_EARLY_CLEANROOM_JAR` to bind the original
Loader method, preserve the complete prefix and original method, and verify the
stop before construction dispatch. These are extraction tests; they do not prove
native selection or mixin application. The same supervised tool's
`--stage selection` continuation qualifies the bounded selection stage with
actual DEFAULT LoadController and late OreDictUnifier definitions, native method
owners and calls, required containers and no construction dispatch. Its domain
response remains incomplete for material initialization.

The `--stage construction` continuation copies the complete original Loader method,
with two observations and all original frames, locals and exception regions intact.
`NativeLoaderPrefixTest` compares the entire method after removing only those two
calls and checks the original event arguments and final state transition. These
preservation tests do not qualify construction or Groovy bootstrap. The baseline now returns with all thirty selected containers constructed.
Bounded native cause frames, real container states and cache measurements remain
available in the incomplete result. `NativeConstructionStageTests` verifies that
partial/returned construction cannot close the unqualified stage or hide changed
saved-source acknowledgement and missing selection evidence.

The same native test support accepts `AXIOM_SELECTION_NATIVE_CANDIDATE`,
`AXIOM_SELECTION_ENGINE_HOME`, `AXIOM_SELECTION_JAVA_HOME` and a new
`AXIOM_SELECTION_REPORT_ROOT`. The candidate must have passed the selection-stage
checker and still match the current bound inputs. Run
`validation.tests.test_axiom_native_early_stage.NativeSelectionExecutionTests`
through `python3 -m unittest -v`. It checks the whole saved baseline, original
blacklist and dependency failures, optional late-mixin configuration, required
hook removal and manual correction in a fresh worker. Supplying both EARLY and
SELECTION input groups runs both native classes in the complete module invocation;
absent groups report skips. No test invokes the worker entry directly.
`NativeEarlyDiagnosticsTest` exercises a freshly
compiled `NativeEarlyClassSpace` supplied on the test classpath; an ordinary
engine-only test invocation skips those host-observer cases. Report these skips
explicitly. It verifies passive overflow handling and retained-record sizes using
the actual JSON transport, including metadata, Unicode and throwable traces.
The Python early-stage evidence and input-preparation tests belong to the
existing fast validation partition; native execution requires the explicit inputs
above and otherwise reports its skip. These checks cover original artifact custody,
complete saved configuration preservation, callback/definition evidence and
deferred claims; passing them does not qualify native execution.

The `--stage groovy` continuation uses the complete saved program and existing
admission, original Foundation VM preparation, Loader preInit prerequisites and
the transformed Groovy dispatch hook. `NativeGroovyInitializationPrefixTest`
checks original method preservation, exact raw/deobfuscated method pins, retained
prerequisite calls and refusal of missing or changed hooks. Its controller fixture
is structural evidence; the supervised native stage establishes actual Mixin
application and invocation. `NativeGroovyStageTests` keeps native compiler errors,
console overflow, ownership and prerequisite gaps explicitly incomplete.
`NativeConsoleCaptureTest` verifies bounded console evidence and its full-stream
digest, including explicit overflow. The native baseline currently returns from
the Groovy hook with original ownership and clean preInit compilation after the
profile supplies Bubbles and GTFO. The existing native diagnostic observer also
captures Groovy file-only messages and compiler locations; its engine index
retains reached script definitions and preprocessing outcomes. The production
material command shares that original entry; installed completed-scope and
material-completion acceptance remain open.

`NativeGroovyExecutionTests` uses the same supervised test support with
`AXIOM_GROOVY_NATIVE_CANDIDATE`, `AXIOM_GROOVY_ENGINE_HOME`,
`AXIOM_GROOVY_JAVA_HOME` and a new `AXIOM_GROOVY_REPORT_ROOT`. It requires
a source-current candidate passing the bounded Groovy-stage checker. Readiness
flags cannot replace verified constructed instances, native plugins, Susy effects,
script ownership or complete diagnostic/index observations. The cases preserve saved locations for a file-only warning/error and
a compiler missing import, then verify saved correction in a fresh worker.
All results remain incomplete for material initialization.

`--stage preinit` uses the same raw inputs and supervisor for the complete original
preInit continuation. `NativeGroovyInitializationPrefixTest` verifies preservation
of every original Loader/controller instruction after removing only passive
observation calls and normalizing the copied dispatch target. The complete
continuation rejects an already installed shorter or complete prefix.
`NativePreInitStageTests` keeps earlier
Groovy evidence separate from later native failures and requires one dispatch,
native mod states and returned non-recipe registry events. The raw host now compiles
the shared material, property, custom-item, fluid and generated-content observation
helpers. Complete current membership catalogs, selected property witnesses and
material/fluid storage bindings are observable after preInit. Prefix items,
material blocks and ore blocks retain original Forge identities and selected native
stack/state/property round trips. Named and numeric material-registry differences
remain explicit. Exact native method descriptors avoid resolving unrelated CLIENT
signatures. Generated-form unifier lookups remain observations of the current phase;
the original recipe event populating them belongs to R6. Full saved-edit acceptance
and supported-scope qualification remain open. The stage checker requires explicit
native catalog, named-registry, storage-binding, custom-owner and generated-identity
evidence; a preInit return alone cannot pass it.

The native stage retains complete console and diagnostic capture without MVP
size targets. Focused console and supplied-host diagnostic tests verify complete
large content, reconfiguration continuity and later errors. Explicit bounded
captures remain controlled regression utilities. Source-only engine tests cannot substitute for the supplied native host.

`NativeStageSnapshotsTest` verifies lossless earlier-stage/diagnostic references,
including source locations, Unicode, nulls, earlier warnings and later failures,
and lossless base64url encoding of all five catalogs' SHA256 fingerprints.
`NativePreInitStageTests` verifies the Python expansion without mutating the receipt.
Expansion restores the earlier stage objects and both complete diagnostic channels;
it does not promote lifecycle or effect acceptance. PreInit retains complete
material/fluid/prefix-item/block catalogs without MVP byte or entry-count targets.
Failed observations return explicit unavailability rather than partial membership.


The existing `tools/axiom_material_runtime_smoke.py` accepts `--saved-program`
with a complete ordinary archive for the original pack runtime. Supply the usual
`--java-home`, `--engine-home`, `--runtime-home` and new `--report`. It invokes
`Main material-program` through its normal supervisor, preserving baseline,
material/form/fluid change, native material failure, file-only warning/error,
compiler missing-import and correction inputs in fresh workers. It verifies current
source acknowledgements, original complete preInit and non-recipe registry events,
current native catalogs and material/fluid bindings. A chained invalid color call
retains the original compiler's preceding-line stack location; it is not rewritten
to the edited token's line. Positive cases require accepted/completed startup
with complete scoped evidence; native compiler/thrown/logged errors require
source-error/native-failed with original saved locations. Resource or observation
limits remain incomplete and cannot supply a positive acceptance case. This
production evidence does not qualify recipes or the installed IDE journey.

The ordinary saved-program lane also toggles the selected GT configuration's
`generateLowQualityGems` setting. Its native Diamond witnesses must gain original
chipped/flawed forms when enabled and lose them on manual correction. Groovy bytes
remain unchanged. This proves that selected configuration consumer and its generated
forms, not every captured configuration field. Repeat `--saved-case NAME` to select
specific cases for a focused run; omit it to run the complete lane.

Saved content cases add a helper class imported by the ordinary material producer,
two dependent materials, a fluid and a custom item in the existing post-material
listener. Modification changes native material color, fluid temperature and item
stack size. Deletion removes the helper and its callers; correction restores the
valid modified sources. The complete ordinary checkout accompanies every case.
`saved-content-addition`, `saved-content-modification`, `saved-content-deletion`
and `saved-content-correction` require current native material/fluid membership
and the actual custom-item owner and stack-size property. Compare retained native
responses with the existing effect comparator to distinguish additions, changes,
removals and exact correction from source edits alone.

`saved-content-broken-reference` retains the original compiler error when a saved
helper is deleted but its import remains. `saved-item-native-failure` and
`saved-fluid-native-failure` require the original exception cause and saved frames
for zero stack size and zero temperature. `saved-content-logged-error` preserves
GT's original Groovy error for legacy component syntax even though material
registration continues. These checks neither repair source nor classify incomplete
initialization as a successful check.

`NativeStartupScopeTest` checks verdict decisions for complete native evidence,
located source errors, missing configuration/effects, unrelated failures and
optional expectations. These fixtures test policy, not native execution.
Console and diagnostic tests retain long compiler cascades and explicit overflow;
Java/Python snapshot tests require lossless text restoration and preserve the
original receipt. MVP resource targets are now suspended; earlier limits remain
part of each historical receipt, not acceptance requirements for new runs.

`NativeStageSnapshotsTest` covers lossless production collection references and
trailing bootstrap failures. The Python expansion restores those fields without
changing receipts. `MaterialRegistrationEffectComparisonTest` compares generated
catalogs through the production encoding and refuses missing inventories without
inferring deletions. Custom-item references resolve at the existing execution path.

Selected-observation checks must use the same complete saved archive and normal
`Main material-program` supervisor. Exercise native material values, properties,
typed property getters, flags, generated/selected/eligible forms, ordinary ore drop,
fluid storage and selected prefix-queue membership. Include native positive and
negative facts, changed source/configuration, correction and source failure with
expectations left unevaluated. Unknown vocabulary and absent values remain distinct
from a known mismatch. Compare complete effect fingerprints with selector-free runs
to verify that observations do not change registration. A retained-evidence
expectation evaluation is useful compatibility evidence but is not a fresh native
command. Keep every resource termination and unsuccessful matrix visible.

Java/Python snapshot tests cover selected collections sharing the execution envelope,
including null formulas, typed scalar tokens, empty forms and ordinary-drop records.
Round trips preserve the source object and refuse unavailable referenced fields.

`tools/axiom_native_root_stage.py --stage recipes` is an unqualified diagnostic
continuation through the original `Loader.initializeMods()` after completed
preInit. Use the same complete saved program and required-context inputs as the
preInit lane. Original mapper/recycling and ERFTemps operation admission now
passes the earlier call boundary; guarded Boolean conversion lets the unchanged
pack compile ModifyRecipeMaps and reach `GTRecipeHandler.removeAllRecipes`.
The selected BOP lifecycle now registers the configured native biome set, and
GT's saved biome-map definitions bind to those original objects. Remaining recipe
operation admission and effective recipe collection are affecting gaps. Keep Workbench context failures
separate from invalid developer source. The stage's
checker cannot certify recipes from a returned initializer or readiness flags;
effective registry acceptance remains unimplemented. The ordinary preInit lane
provides the regression check for the preserved startup path.

`MaterialMapperBindingsTest` and `MaterialAdmissionPolicyTest` retain exact mapper
selection, binding replacement/removal and unrelated-call refusal. Policy tests
also check original effective-getter lookup, unchanged getter
evaluation count and native exceptions, and refusal of custom property selectors.
The existing `MaterialSourceAdmissionTest`, `MaterialBytecodeGateTest` and `WorkerIsolationTest`
cover candidate compilation and host/process/network boundaries. Native recipe
regression uses the complete saved archive, a changed callback that attempts an
unselected mapper, and restored source; fixture refusal does not certify complete
recipe registration.

`NativeRecipeFunctionObservationsTest` uses `AXIOM_EARLY_GROOVYSCRIPT_JAR`,
`AXIOM_EARLY_ICBM_JAR`, `AXIOM_EARLY_BDSANDM_JAR` and
`AXIOM_EARLY_BACKPACKS_JAR` for the exact selected original artifacts. Missing artifacts
produce explicit skips. It compares every original method before/after observation,
rejects changed factory routes, and checks native callback identity, captured
values, copy/return behavior and exception preservation without observer evaluation.
These tests supplement native recipe execution; they do not qualify its completion.

The bytecode conformance lane also compiles ordinary spread-argument calls with
the original Groovy compiler in guarded and unchanged classloaders. It checks
argument order, retained references, null/empty and primitive-array behavior,
single evaluation and native invalid-spread errors. Wrong helper signatures and
unselected collection/formatting callbacks refuse before evaluation.

`MaterialBytecodeGateTest` checks the exact Boolean adapter rewrite, alternate
owner/descriptor/opcode refusal, native truth results, short-circuit evaluation,
original conversion exceptions, and refusal before an unselected `asBoolean` call.
`NativeBiomeObservationTests` rejects partial configuration coverage, wrong
artifact/registry identities and biome-map values inconsistent with saved JSON.
Native regression uses an unchanged complete archive, a saved disabled biome,
and restored source; the configured disabled entries remain disabled. Ordinary
preInit requalification covers the expanded selected context. Installed package
resources must include the same observer used by the conformance tool.

The native compiler and addon scanner follow the suspended MVP resource policy:
no execution deadline or scanner heap target, and complete compiler diagnostics.
The outer engine build/smoke wrapper has no elapsed-time target. Existing input
structure and artifact identity checks remain applicable. `MvpResourcePolicyTest`,
`MainTransportTest` and `NativeConsoleCaptureTest` cover inherited worker resources,
JVM ergonomics, complete transport/console output and termination handling.

### Expanded-context diagnostic regression baseline

The explicitly provisioned Groovy diagnostic cases retain the pinned pack's
original FML signature errors. They may use the exact failed construction/Groovy
baseline as a negative fixture: the strict checker failures and complete native
error rows stay recorded and must match the declared expectation. Saved compiler
and logged errors must remain attributable, and correction restores the unchanged
baseline. This does not qualify clean construction, resolve the narrow checker's
incidental contained-LadyLib source refusal, or establish whole-pack validity.
Installed complete-scope acceptance remains separate.
