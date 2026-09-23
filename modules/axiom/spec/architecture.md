# Architecture

Status: target architecture. See [first-slice scope](first-slice.md) for what is
implemented; the broader capabilities below are not all available.
The [initialization-check MVP](initialization-mvp.md) defines current delivery
priority: saved edits, native initialization without Minecraft, readable errors
and IDE highlighting. Machine simulation below is a longer-term target, not a
prerequisite for useful initialization feedback.
The [source-target foundation](source-targets.md) now packages profile-owned
source inputs and verifies loader membership without claiming registry closure.

## Product boundary

Axiom is a Workbench module with a separately usable Java engine. Its immediate
purpose is native initialization feedback for saved developer edits. The broader
target includes constructing effective recipes and evaluating technical behavior
against the source composition of a selected Supersymmetry target.

The default workflow is developer-written source edited in an IDE. Axiom reports
findings and explains machine behavior; generated recipe authoring is not required
for using it. It does not automatically change scripts, rebalance recipes or
upgrade dependencies.

## Ownership

| Owner | Responsibility |
| --- | --- |
| Axiom Java engine | Isolated native compilation/initialization, registration-effect observation and source-linked diagnostics; later recipe/machine queries remain separately qualified |
| Supersymmetry profile | Selection and packaging of exact pack/mod/platform bindings, configuration, target policy and required context |
| Axiom Workbench integration | Module registration, invocation translation and result delivery through supported API ports |
| Workbench Core | Toolchain provisioning, installation, process/service coordination, cancellation, recovery and cleanup |
| IDE/CLI clients | Source navigation, diagnostics and explicit user queries; no independent validity algorithm |

Original native implementations execute within Axiom's worker. The profile
selects their supported source/artifact identity and required composition; it
does not replace them with Python simulations or a second Java loader. The older
bounded recipe interpreter is not the authority for native initialization.
Standalone use must accept an explicit target package without
depending on Workbench services or a particular workspace layout.

The current Workbench ownership and registration contracts are documented in
[Core and modules](../../../docs/architecture/CORE-AND-MODULES.md) and
[module development](../../../docs/guides/module-development.md).

## MVP execution architecture

The [MVP contract](initialization-mvp.md#required-stack-and-scope) names the
required stack: GroovyScript, CleanroomMC/Forge infrastructure (including
MixinBooter and IVToolkit), GTCEu, GCYM, Susy-Core, SussyPatches and Supercritical.
It requires faithful loading/initialization for supported developer edits, not
every installed mod's lifecycle or a new general-purpose loader.

1. Core captures the current saved workspace and resolves profile-owned native
   inputs and the intended JVM. Provisioning is reusable; mutable initialization
   state belongs to a fresh isolated worker.
2. Axiom composes original native platform/classloading, configuration and
   transformation behavior needed by the declared scope. Dependency/container
   state must come from the appropriate native behavior, not inventory guesses.
3. Original GroovyScript compilation and native lifecycle/registry code execute
   the admitted source with original bindings, expansions, owners and order.
   This includes Minecraft classes required for registration, without starting
   the game loop, renderer, world or client/server entrypoint.
4. Passive observation records actual registration/mutation/removal effects and
   native compiler, logged and thrown errors with saved-source provenance.
   Instrumentation must not replace registration semantics or alter dispatch.
5. Existing CLI/IDE clients present scoped completion, native failure or an
   incomplete check, with source highlighting and stale-input protection.

Presence, native loaded state and executed/qualified callbacks are distinct.
Required environment seams must be explicit and source-backed; missing required
behavior cannot be filled by favorable stubs. Tertiary integrations may remain
deferred only when their omission does not affect the checked scope. A successful
subset is never a whole-file/whole-pack validity claim. Current incomplete
results remain incomplete until the scoped execution is implemented and qualified.

## Longer-term logical components

1. **Target admission:** checks source/dependency identity, configuration, available
   registries and coverage before admitting a query.
2. **Construction engine:** evaluates supported recipe-definition code and
   produces effective registrations, removals, dynamic resolvers and lineage.
3. **Machine catalog:** resolves each registered processor through its factory,
   concrete/inherited/anonymous logic, handlers and applicable injections.
4. **Behavior engine:** applies original matching, lookup, gates, preparation,
   processing and completion semantics to isolated domain state.
5. **Diagnostic layer:** explains decisions using original source locations,
   rule references, allocations, requirements and ordered state changes.
6. **Standalone/integration interfaces:** invoke those same components; no
   alternate approximate engine for IDE use.

These are logical boundaries for later capabilities, not six MVP packages or
services. They do not authorize recreating Cleanroom/Forge loading semantics.
Keep a cohesive Java implementation and narrow public contracts.

Axiom executes on the [profile-pinned native JVM](native-runtime.md). It does
not implement a bytecode interpreter, Java heap, verifier, linker, Class mirrors
or standard-library bootstrap. The JVM provides those semantics.

The earlier recipe-construction endpoint still uses the bounded Groovy AST
model. Native GroovyScript/material execution exists with explicit composition
gaps; native recipe registration/removal remains an integration gate, not a
capability granted by choosing the correct JVM or relabeling the AST endpoint.
[Retained source research](registration-research.md) identifies the actual
loader, transformation and registration requirements.

## Execution model

Source checking and explicit machine queries share construction and behavior
implementations. Fast editing feedback may use incomplete syntax/type analysis;
it must be labeled separately from completed semantic validation.

For stateful processing, evaluate isolated machine state with an explicit clock,
ordered external events and supplied random choices or qualified random state.
Logical ticks do not require a Minecraft server. Do not skip ticks or alter event
ordering merely for performance unless equivalence is established for that path.

Environment-dependent logic consumes explicit facts or extracted environment
rules. A supplied `structureFormed` value is an assumption unless an admitted
formation evaluator established it. Absence of a dependency cannot be filled by
a favorable stub or a null-world fallback.

Retain state mutations that happen before rejection. No speculative query may
modify the original developer input, live world, installed package or source
checkout. Independent runs must not leak registration, cache or random state.

## Definition and state packages

A target package needs exact source/artifact identities; active module/config and
injection composition; registry and ore/unification information; construction
phase policy; effective recipe/map state; machine definitions; and coverage.

Do not flatten all recipes into a static list: dynamic map resolvers and custom
non-map processing must remain executable, source-bound behavior. Preserve typed
NBT, capability-dependent semantics, handler ordering and callback identity.

Package data must be sufficient to reproduce supported queries outside a source
checkout. Any unavailable value, callback or source transformation remains an
explicit dependency gap. Discovery from a previous game run is neither required
nor an implicit fallback.

## Packaging and lightweight operation

Do not launch rendering, a game/server loop, network activity or unrelated world
systems. This is an execution boundary, not permission to discard classes or
initialization dependencies required by supported registration. Cache source
compilation and immutable target data by full dependency
identity; invalidate transitive dependents after changes. Optimize only after
source-equivalent behavior and realistic measurements exist.

The Java library/application uses native JVM build/version metadata. Workbench
integration uses its existing native Python distribution boundary unless that
host contract is deliberately changed. Compatibility and exact artifact identity
must be declared between them. The release tooling explicitly discovers Axiom's
native Gradle version and artifact alongside its thin Python package. It does
not automatically register arbitrary Java projects.

The real engine and integration now have native manifests and entry points.
Filenames stay stable and versions live in owning native metadata. Linux is
the initial qualification target; WSL and
macOS remain explicit future targets.

## Source execution security

Groovy/Java recipe code is executable code. Evaluation must use an isolated
process with explicit filesystem access, no ambient credentials, disabled
network/process execution by default, and CPU/time/memory/output limits enforced
outside the script language. Class-loader restrictions alone are not a sandbox.

Axiom's standalone application must define its own execution limits; Workbench
may supply stricter host limits. An operation denied by policy or stopped by a
limit is incomplete/unsupported, not proof that the recipe is invalid. Unknown
reflection, native code or unmodeled APIs cannot silently bypass the boundary.

## Independence and non-goals

No legacy adapter to the retired validation strategy is required. Faithfully
supporting the selected upstream recipe APIs is an input requirement, not a
reason to preserve old Workbench internals.

Axiom does not claim balance, progression quality, scientific correctness,
eventual success under unspecified future events, or complete game equivalence.
Those can be separate analyses with their own assumptions.
