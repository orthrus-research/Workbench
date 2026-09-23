# Native initialization-check MVP

Axiom lets a Supersymmetry developer save an edit, run a command from their IDE,
and inspect native initialization errors and accurate source highlighting without
launching Minecraft. Developers own their edits; Axiom does not repair source.

This document defines product scope, completion criteria and supported result
meaning. Individual capability specifications retain their narrower evidence
boundaries; historical recommendations in them do not replace this delivery order.

## MVP definition and ownership

The input is an ordinary saved checkout, including original Groovy, configuration,
loader configuration and cross-loader imports. No intent file or material selector
is required. A completed check means complete execution of a declared supported
initialization scope, not complete Supersymmetry startup or whole-pack validity.

The required stack is GroovyScript, CleanroomMC/Forge infrastructure (including
MixinBooter and IVToolkit), GTCEu, GCYM, Susy-Core, SussyPatches and Supercritical,
plus dependencies demonstrably required by the checked operations. Artifact
presence, native loaded state, executed callbacks and qualification remain distinct.

The pack profile also selects the original Universal Tweaks artifact for its
configuration-selected registry-prefix transformation and common/SERVER lifecycle.
Its original early and late mixin selection participates in native initialization.
Diagnostic comparisons must retain rejected registrations and account for this
transformation; fewer warnings alone do not establish equivalent registry state.

| Owner | Responsibility |
| --- | --- |
| Core | Provisioning coordination, process control, capture/storage, cancellation and cleanup. |
| Pack/platform profiles | Exact source/artifact, configuration, supported scope, side and JVM selection. |
| Axiom | Original native initialization, bounded execution, registration observations and diagnostics. |
| Existing CLI and IDE clients | Invoke checks, show scoped results and navigate exact saved source. |

Preserve the original compiler, configuration consumers, transformations, owners,
dispatch order and registries. Do not substitute a replacement loader, handwritten
registration model, favorable loaded flags, fake registries or no-op callbacks.
Native Minecraft classes needed for initialization may execute; client/server
entrypoints, rendering, worlds and game loops do not.

## Qualified MVP scope

The qualified MVP candidate covers the selected Supersymmetry 0.1.16.15 source at
`3e83cd7bad57bd4c424de4e6cc707ab02fe32f54`, its pinned original addon inputs and the
`supersymmetry:material-authoring-pack` SERVER/common context. These are selected
versions, not a claim about current upstream releases. Qualification binds exact
saved source, configuration, engine, runtime, JVM and client inputs; changing those
inputs requires checking the applicability of the retained evidence.

| Area | Qualified behavior |
| --- | --- |
| Source and execution | Complete saved Groovy/configuration capture, independent native digest/count acknowledgement, fresh workers and retained diagnostic/source identity. |
| Native platform | Original early/selection, construction, preInit and recipe initialization through the pack-selected native entry, preserving owners, registry events, callbacks and transformations. |
| Materials and items | Actual material/fluid bindings, custom items and generated prefix/material-block/ore collections; saved edits, native failures and corrections change or restore effective native state. Earlier addition/removal and generated/custom coexistence cases remain covered by unchanged-input regression evidence. |
| Saved IDE workflow | Installed CLI and actual VS Code/IntelliJ error/correction, source/history and stale/unsaved-source workflows. Checks need no intent file or material selector. |
| Recipes | Native producers and removers reach effective GT lookup storage, crafting order and furnace mappings. Addition, modification, removal, failure, correction and source deletion are verified. Ten machine families cover simple conversion through sixteen ordered item inputs and four fluids, with complete assembly-line detail in both IDEs. |
| Verdicts | Original pack errors, native source/compiler failures and affecting context gaps remain distinct. Qualification requires complete native effect observations for the declared scope; caught unsupported operations cannot erase incomplete coverage. |
| Result access and retention | Immutable complete archives, versioned section readers, paged/chunked details, full record export, rebuildable indexes and explicit historical comparisons. Core supplies inventory, pins, preview/retirement/restore/purge, verified local bundles, reconciliation and user-selected finite retention. |

Temurin 25.0.4+7 Linux x86_64 is the deliberately selected Workbench MVP runtime.
Fresh Core setup acquired this JVM and all 202 original inputs into empty managed
state, installed the independent engine ZIP and assembled the native runtime.
Encoded acquisition URLs retain the original artifact filenames and hash pins.
The Linux runtime and IDE workflows were exercised on the recorded WSL host;
this does not add general WSL, Windows, macOS or other-JVM support. This Workbench
choice does not identify an upstream pack-installed JRE. The Cleanroom platform's
`cleanroom-provisional` status remains separate from this candidate's scoped MVP
qualification.

The Windows x64 policy separately pins Core-provisioned Temurin 25.0.4+7 bytes.
Its JDK and compiler file inventory, Core engine ZIP installation, long-path
custody checks, and installed `coverage` command have direct host evidence from
the tested ASCII state path. A Unicode state path allowed Java's version probe
but failed on main-class DLL loading; Core now refuses that runtime during its
class-loading probe even when a DOS short path exists.
Source evaluation on Windows is not qualified. In an isolation prototype, Java
could read the JDK security file, but its canonical-path lookup failed before
Axiom returned a native result. The installed command refuses Windows source
evaluation until isolated native cases pass there.

`setup --prepare` resolves the profile's raw SERVER and original native inputs
through Core. An independent engine ZIP or supplied engine directory provides
the engine. Setup compiles packaged sources and assembles the runtime under Core
supervision, then binds and revalidates the runtime, engine and selected JVM.
Initial IDE preparation may request explicit input locations; routine checks reuse
the managed setup without repeated runtime-path prompts.

The unchanged selected pack retains its original native errors. A completed
supported capture can therefore report **native initialization failed** while
preserving complete registry observations. MVP qualification verifies that these
outcomes are faithful; it does not certify an error-free pack. Broader context,
platform and release qualification flags are not promoted by this acceptance.

Normal recipe CLI responses carry a compact snapshot view. Findings and native
details are retrieved through Core; complete original results and individual large
records remain exportable without requiring one complete IDE string. Cancellation
remains available during publication after native execution ends, and recovery can
publish the retained result without repeating native execution. Completed-scope
measurements are retained without a full-check speedup claim. Optimization follows
MVP qualification.

## Delivery milestones

| Milestone | Exit | What it does not claim |
| --- | --- | --- |
| Useful startup-edit checks | Original required context, construction and material/item/fluid registration complete for the declared scope; installed saved-edit acceptance and reproducible setup pass. | Native recipes, complete MVP or publication approval. |
| Functional MVP | Accepted for the selected context: original recipe registration/removal, saved addition/change/deletion/failure/correction evidence, complete versioned result access and managed history. | Machine-slot/circuit simulation, gameplay or all-mod parity. |
| Qualified MVP candidate | Accepted for the selected context: exact intended JVM, completed-scope measurements, affected installed/IDE/distribution/canonical validation and all mandatory acceptance obligations pass on frozen inputs. | Automatic publication, an unmeasured speed claim or CLIENT-only validity from SERVER evidence. |

Delivery follows these dependencies:

1. Establish early transformation through original bootstrap/tweak ordering and
   the complete `FMLDeobfTweaker` transition. Prove actual class definition, not
   only registration of transformers or separately transformed byte arrays.
2. Establish native mod/container/API/dependency/configuration state and late
   mixin selection. Stop before construction event dispatch. Early INIT readiness
   alone does not qualify late mixins used by the required addons.
3. Execute original construction and Groovy bootstrap, replacing temporary owner
   and module-state setup on the accepted production path. Keep newly reached
   compiler/definition routes under the existing admission and isolation policy.
4. Complete material/post-material callbacks, custom/generated coexistence and
   block/item/fluid registry events. Prove the ordinary unchanged-checkout baseline.
5. Qualify startup-edit outcomes and completed-scope reporting through the installed
   command and existing IDEs; do not merely remove the current incomplete guards.
6. Execute native recipe prerequisites and script stages, then qualify effective
   recipe addition, change, removal, native failure and developer correction.

Reproducible preparation and qualification of the selected JVM are independent
integration work that must finish before the corresponding installed milestone.
Measure startup checks once they complete; add recipe measurements once that scope completes.
Final qualification follows the complete supported workflow, not isolated probes.

## Acceptance criteria

| Gate | Required proof |
| --- | --- |
| Ordinary baseline | Complete declared required-stack scope over original saved input; no fixture substitution or unexplained affecting dependency gaps. |
| Startup edits | Material, item and fluid additions, modifications and deletions change actual identities/properties/generated forms; dependent-reference failures are preserved. |
| Native diagnostics | Compiler errors, thrown errors and logged-without-throw rejection retain native messages and supported source/cause locations. Developer correction removes the relevant failure without hiding legitimate warnings. |
| Recipe edits | Original registration/removal changes the effective native recipe registry. Successful compilation, builders, declaration counts and saved-file deltas are insufficient. |
| Routine command | Profile/Core-bound setup, current saved source, no required intent/selectors, outside-checkout operation, both supported IDEs, cancellation and cleanup. |
| Freshness and isolation | No shared mutable registry/metaclass state between attempts; changed/unsaved buffers cannot receive stale result markers; historical source remains inspectable. |
| Complete result access | Versioned retained evidence with summary/diagnostic/detail access in both IDEs, useful native messages and complete reference resolution without monolithic result strings or truncation. |
| Evolution | Added/removed/unknown sections and schema changes preserve historical meaning; unsupported coverage/comparisons stay explicit and rebuilt indexes cannot manufacture missing evidence. |
| Storage lifecycle | Visible allocation/retention reasons, pinning, preview/recovery/reclamation, verified local export and reconciliation; enabled finite retention bounds eligible history while protecting active and required evidence. |
| Qualification | Exact JVM, source/artifact/configuration binding, relevant original-versus-observed and containment regressions, frozen installed/IDE/distribution/canonical checks and public package boundaries. |

Existing narrow-family passes remain regression evidence; they do not waive
acceptance for newly composed contexts. Manual correction means a developer or
test author edits saved source and reruns; it is not an Axiom repair feature.

## Result contract

Headlines distinguish **initialization completed**, **native initialization failed**
and **check incomplete**, naming the phase and supported scope actually checked.
Invocation completion and complete input capture are not initialization success.

An affecting configuration/dependency/transform/admission gap remains incomplete.
An unrelated deferred phase can coexist with a qualified scoped result,
but stays visibly unchecked. Do not weaken an affecting requirement to obtain a
pass, or classify an Axiom bootstrap limitation as invalid developer source.
Startup completion now requires the original selected configuration bindings,
native callbacks, diagnostics and complete effect observations. Missing affecting
evidence remains incomplete. Setup readiness and native scope acceptance remain
distinct from final MVP qualification.

Group related source frames under their native diagnostic. Highlight only
supported saved lines, never guessed ranges or logging sites misidentified as
developer call sites. Preserve error causes, original warning messages, partial
state and logged failures even when native methods return. Observations must not
change dispatch, evaluation count, ordering or registry effects.

Recipe observations preserve the state left by original callbacks, including
their independent registrations. In the selected context, removing a
distillation-tower or assembly-line parent recipe does not automatically remove
its generated distillery or scanner recipes. Parent validation can also fail
after those callbacks have registered children. Reports retain those effects
alongside the native diagnostic. A saved
correction or source deletion is checked in a fresh worker.

Core retains complete original input files and archives; the native response
independently acknowledges their digest/count. Single and paired responses retain
complete evidence without an MVP byte target. Never silently truncate observations
into an empty or apparently complete registry. Full-pack inline pairs remain
outside the required primary single-run error/correction workflow.

## Result history and storage lifecycle requirements

These requirements extend the MVP delivery scope. Automatic retention requires
explicit user policy selection. Capture coverage remains distinct from candidate
qualification. Historical readers resolve the original declared scope and report
current interpretation separately from capture coverage. Versioned derived views
and index rebuilds preserve original evidence. Explicit retained comparisons
separate compatible stored changes, changed applicable scope and incompatible
observations; selected crafting graph comparison resolves referenced children.
These reader capabilities do not establish support for an unqualified pack version.
The [snapshot/read contracts](../../../docs/architecture/CHECK-SNAPSHOT-CONTRACT.md)
have schemas, validators and Core publication/query/rebuild APIs. CLI and IDE
saved-check workflows use snapshot summaries and paged detail reads. Complete raw
results remain retained alongside the archive/index until whole-attempt retirement;
their allocation is additional. Core exposes manual check inventory, pins, verified
local evidence export and recovery through the existing storage commands. Native recipe and combined lifecycle acceptance pass for the selected context.
Completed-scope measurements and final candidate qualification pass for the selected context.

Each completed capture must retain exact inputs, producer/context identities,
outcome, completeness and versioned section descriptors. Saved observations remain
immutable while retained. A specific cleanup decision or enabled retention policy
may retire eligible history, with its availability updated explicitly.

Clients load summary/findings first and retrieve details through Core. They may
show the last completed result while checking, identified by its original source
and pack context. All views and detail requests stay bound to one snapshot; old
success or source markers cannot apply to different current inputs. Not loaded,
empty, inapplicable, unsupported, unavailable and expired remain distinct.

Pack and reader versions can add, remove, split or change observation sections.
Expected/applicable scope controls completeness; observer failure cannot appear
as a removed registry. Preserve native types, ordering, duplicates and shared
references. New derived indexes or comparison views retain their original input
identity and interpretation version without rewriting historical evidence.
Missing historical fields remain unknown. Storage compatibility does not establish
native support for another pack or require every legacy native runner forever.

Core must account for snapshots, saved inputs/runtime artifacts, derived indexes,
caches, active work, quarantine and maintenance records. Show physical allocation,
protected reasons and group-level reclaimability without double-counting shared
content. Offer pinning, cleanup preview, retirement, recovery, permanent reclamation
and reconciliation through existing workflows. Quarantine itself frees no space.
Verify a complete local export before releasing its local source; distinguish
inspectable evidence from a bundle that retains native reproduction prerequisites.
Managed data must remain discoverable after its project/profile/module is inactive.

Automatic retention requires a visible, user-selected policy with finite count/byte
preferences and completed-trash lifetime. Protect active work, explicit pins and
required proof/recovery dependencies; explain protected overages and capacity
failures. Count and retire unused derived generations and terminal maintenance
records when their obligations end. Do not recursively capture previous history
or materialize every possible comparison pair by default. Policy-driven removal
must record actual effects and leave truthful expired-history navigation.

Retention preferences apply to eligible old history and caches. They do not
restore suspended native execution/output targets or permit truncating new results.
Reconciliation can repair derived indexes/accounting; it cannot invent missing
native evidence or treat unknown ownership as permission to delete.

## Scope and weight controls

An extra dependency is justified only by a named supported operation and native
source/runtime evidence. If original required construction would effectively
require starting the full pack, that is an architectural decision point, not
permission to fabricate state or silently expand to every mod.

Preserve complete saved source and meaningful integration conditions. Do not
prune statements, source roots, listeners or mixins just to obtain a positive
result. Source availability across loaders does not execute deferred loader bodies.
Common/SERVER evidence does not qualify CLIENT-only registration behavior.

Outside this MVP: LSP repair or headless LSP hosting; automatic repair; blueprints;
full machine/slot/circuit/world simulation; all-mod parity; unrelated embedded
library inventories; large inline full-pack comparisons; and macOS/WSL support.
LadyLib/Gaspunk and GTFO/other tertiary callbacks enter the critical path only
where a checked operation actually requires them. Existing dependencies and
evidence are not deleted merely because their broader lifecycle is deferred.

## Performance and distribution

MVP development temporarily suspends Workbench resource targets: CPU and wall
time, JVM memory/processor settings, address space, file writes/descriptors,
transport size, and console/diagnostic/catalog allocations. Installed Axiom
commands use Core supervision without a deadline or I/O budget. JVM ergonomics
and inherited host resources still apply. Cancellation, fresh workers, cleanup,
complete diagnostics and source custody remain required.

Resource budgets and optimization follow MVP. The earlier 34.8-second/80% feedback
target is deferred. Record successful scope, hardware, exact JVM and setup cost
when measuring; a fast early failure does not prove completed-check performance.

Linux is the initial qualification target. Public packages must not contain
credentials, local source captures, generated runtime state or unapproved third-
party inputs. Product qualification is separate from GitHub publication.

## Detailed specifications

- [Installed command, source capture and result protocol](material-program-preflight.md).
- [Pack context, selected inputs and native integration boundaries](pack-material-context.md).
- [Raw SERVER root evidence and early/late transformation boundary](native-root-class-space.md).
- [Native initialization hooks and observations](initialization-hooks.md).
- [Native custom items](native-custom-items.md), [GCYM material events](native-gcym-material-events.md)
  and [Supercritical material events](native-supercritical-material-events.md).
- [Tests and conformance](../tests/README.md), [source provenance](../sources/README.md)
  and [Workbench integration](../integration/README.md).

Source-backed acceptance examples remain the pinned pack's material producers,
`ChangeFlags` and `RegisterMetaItems`, with complete saved-program tests. Upstream
[material-order change](https://github.com/SymmetricDevs/Supersymmetry/pull/1878),
[recipe addition](https://github.com/SymmetricDevs/Supersymmetry/pull/2003/files),
[ore-input change](https://github.com/SymmetricDevs/Supersymmetry/pull/2004/files)
and [mixin application fix](https://github.com/SymmetricDevs/Susy-Core/pull/688)
guide useful cases; these examples are not proof of current Axiom support or a
statistical survey of development frequency.
