# Axiom

Native initialization checks for saved developer workspaces, without launching Minecraft.

The product target is **save an edit → run Axiom → inspect native errors and
source highlighting**. Developers own their edits. Axiom does not repair source,
generate fixes, require an expectations file or start a language server. It targets
initialization-time changes that developers cannot dynamically reload.

The [initialization-check MVP](spec/initialization-mvp.md) is the authoritative
product scope and completion contract. It includes native material/item/fluid
initialization **and recipe registration/removal**. Full machine simulation and
the earlier bounded recipe interpreter are not prerequisites or substitutes.

The required stack is GroovyScript, CleanroomMC/Forge infrastructure (including
MixinBooter and IVToolkit), GTCEu, GCYM, Susy-Core, SussyPatches and Supercritical,
plus actual dependencies of the checked operations. Axiom reuses original
compilation, loading, transformations, lifecycle and registration behavior in an
isolated process. It is not a replacement loader or a full-pack startup simulator.

## Current developer workflow

[Native material preflight](spec/material-program-preflight.md) captures the
complete saved Groovy/configuration workspace on each command and presents native
diagnostics through the existing CLI, VS Code and IntelliJ workflows. No material
selector or intent file is required; optional saved expectations remain available.
Core retains verified setup for routine reruns, complete source custody and exact
source identities. Native intake independently acknowledges the saved inputs.
Unsaved or changed source cannot receive stale error markers.

The initialization-check MVP candidate is qualified for the selected
`supersymmetry:material-authoring-pack` SERVER/common context. The
[MVP specification](spec/initialization-mvp.md) defines the accepted behavior and
its boundaries. Qualification covers complete saved-input capture, original
material/item/fluid initialization and effective native recipe registration,
including saved addition/change/removal/failure/correction workflows.

Fresh Core-managed Java, original native inputs, engine installation and runtime
assembly pass. Installed CLI and actual VS Code/IntelliJ checks verify native
errors, exact source navigation, historical results and stale/unsaved-source
protection. Initial IDE preparation may request explicit input locations; routine
checks reuse the managed setup.

Results use compact snapshot summaries and progressive findings/detail reads.
Core retains complete versioned archives, supports full record export and derived
index rebuilding, and supplies explicit history comparison, storage inventory,
pinning, recovery/reclamation and user-selected finite retention. Cancellation
during publication preserves the native result for recovery without another
native execution.

The unchanged selected pack retains its original native errors. Qualification
verifies faithful scoped execution and reporting; it does not make that pack
error-free. Affecting gaps remain incomplete. Whole-game startup, world behavior,
machine execution and all-mod parity remain outside this MVP. Earlier narrow
capability specifications retain their individual evidence boundaries.

Axiom uses the selected Temurin HotSpot 25.0.4+7 Linux x86_64 runtime, exercised on
the recorded WSL host. This does not identify the upstream pack-installed JRE or
establish every WSL2 distribution as a separately qualified host. Windows users
can run the Linux release inside a WSL2 distribution with the checkout and managed
state on its Linux filesystem; the native Windows command is outside this release.
Cleanroom's provisional platform status and release/publication qualification remain separate. Resource targets remain
suspended pending the post-MVP optimization discussion; current measurements
establish no full-check speedup.

## Worker isolation

The local default is Bubblewrap. For a Docker worker, select
`--sandbox-backend docker` on `checks materials run` or `prepare`. Core checks a
local Docker daemon with the `runc` runtime and retrieves the pinned Linux x64
base image if absent. The selected Java and native inputs stay profile-owned;
Core mounts only those inputs read-only into a fresh worker. Axiom records the
selected backend and image policy with its invocation. Docker availability is
required for this explicit choice, including offline runs unless the image is
already present.

`--sandbox-backend gvisor` uses the same Docker worker contract with the
registered `runsc` runtime. The host must first install and register `runsc`
with Docker; Core reports a missing runtime rather than changing backends.
See the [gVisor Docker installation guide](https://gvisor.dev/docs/user_guide/quick_start/docker/).
Core tracks each Docker session under the selected private state root. A later
run removes a worker left by a terminated Workbench process; operators can
request the same cleanup with `workbench sandbox recover --state-root STATE`.
This recovery only removes containers bearing the recorded session identity.

The Docker worker has passed the installed smoke and isolation probes. An
unchanged saved 2,364-file program completed native execution with an incomplete
pack-context result, and its saved invalid-color variant retained the original
native error. This does not qualify complete saved-workspace acceptance. Hosted
CI and gVisor native execution have not yet been observed on this change.

Core can provision and verify the separately pinned Windows x64 Temurin 25.0.4+7
runtime and install the engine at a long managed path. The installed `coverage`
command runs from the tested ASCII state path. Some Unicode JDK custody paths
still prevent Windows from loading Java classes even through an 8.3 alias; Core
rejects those paths during its class-loading probe. Native Axiom checks on
Windows remain unqualified because
the isolated Java process cannot complete the JDK's canonical-path security
initialization. The command refuses source evaluation there. Use the Linux x64
route with scoped native evidence until the Windows isolation lane passes native
cases.

## Start here

### Optional Atlas observation queries

Axiom's Python integration exposes `retained_evidence.admit_retained_snapshot`
for consumers of existing saved-check results. An explicit composition adapter
injects this historical request/source/schema policy into Core's retained reader;
the shared interface is `workbench_api.retained_snapshots`. Axiom does not import
Atlas, and reading a snapshot does not execute the native engine.

Atlas's optional Supersymmetry adapter projects these observations into named
node families and preserves the original outcome, coverage, side and evidence.
It can reopen original records through the same custody-checked reader.
See [Atlas initialization observations](../atlas/README.md#retained-initialization-observations)
and the [Core retained-reader contract](../../docs/architecture/CHECK-SNAPSHOT-CONTRACT.md#optional-domain-consumers).

This integration preserves current native observation limits: some complete
inventories contain only fingerprints; stored recipe/callback data does not
establish executed matching, machine behavior or source-operation causation.

### Implementation references

- [MVP scope, delivery milestones and acceptance](spec/initialization-mvp.md).
- [Saved-workspace commands, setup and current reporting](spec/material-program-preflight.md).
- [Required-stack material context and remaining composition](spec/pack-material-context.md).
- [Original SERVER root class space and transformation boundary](spec/native-root-class-space.md).
- [Native JVM execution and its qualification boundary](spec/native-runtime.md).
- [Java implementation and build](jvm/README.md), [Workbench integration](integration/README.md) and [tests](tests/README.md).
- [Selected source provenance](sources/README.md), [lock](sources/supersymmetry.lock.json) and [notices](sources/NOTICE.md).

## Build the engine

From the repository root:

```sh
python3 tools/build_axiom.py --provision
```

This uses Core to provision the locked JDK, Gradle ZIP and registry utilities,
then runs tests and an outside-checkout smoke. It produces an engine candidate
without publishing. Installed material-check setup
can prepare that independent engine ZIP and the profile-owned original native
inputs through Core, then assemble and retain the runtime. See the
[setup command](spec/material-program-preflight.md) for `--prepare --engine-archive`.
Pass `--sandbox-backend docker` to run the worker-bearing build tests and installed
smoke under Core's Docker selection; `gvisor` requires the registered `runsc`
runtime. The selected backend is explicit and never falls back during a build.
Fresh setup with the selected Temurin 25.0.4+7 and all original expanded-context
inputs passes the installed MVP scope. The two artifact URL paths with literal
plus signs are encoded for acquisition without changing original filenames or
hash pins. Engine, runtime and client evidence remain bound to their exact inputs.
Linux and bubblewrap are required for standalone source evaluation.

The Java version belongs to `jvm/build.gradle.kts`; the independent Python
integration version belongs to `pyproject.toml`. Source filenames stay stable.

## Target and ownership

The selected target is Supersymmetry `0.1.16.15`, commit
`3e83cd7bad57bd4c424de4e6cc707ab02fe32f54`, with pack-pinned GTCEu
`2.8.10-beta`, Susy-Core `0.1.118` and GroovyScript `1.4.3`.
GroovyScript declares Groovy `4.0.30`. These are selected inputs, not claims
about the latest upstream releases or a qualified full registration universe.

Core owns provisioning/process coordination, source capture, cancellation,
retention and cleanup. Profiles own target/context/artifact/JVM selection.
Axiom owns bounded native execution and truthful diagnostics/effect reporting.
IDE clients present the results in the existing Review → Change → Diagnose flows.

## Earlier bounded recipe tools

The Java library and standalone `check`/`query`/`coverage` commands implement
selected-program construction and ordinary MIXER start admission. `target`
verifies profile-owned source packages and explicit candidate edits/deletions;
`platform` inspects explicit bootstrap/library inputs without loading classes.
See [the original implemented slice](spec/first-slice.md) for those commands.

The upstream `Coolants.groovy` fixture uses source-bound admitted builder
operations and supplied registry/machine state. This is a separate bounded
interpreter—not the native initialization executor, not proof of full pack
recipe validity, and not an independent oracle for the MVP. Missing semantics
and registry facts remain explicit. No fake world or Workbench game harness is used.

## Capability and historical references

These specifications retain individual implementation boundaries and source
evidence. Their incremental recommendations do not override the current MVP
contract. The [authoring-value checkpoint](spec/authoring-value-checkpoint.md)
records the earlier rationale for moving from family coverage to saved-edit feedback.

- [Portable source targets and candidate inspection](spec/source-targets.md).
- [Declared dependencies, index integrity and composition gaps](spec/dependency-composition.md).
- [Offline artifact capture and non-executing binary inspection](spec/artifact-inputs.md).
- [Explicit platform/library packages](spec/platform-inputs.md).
- [Registration declarations and material lifecycle dependencies](spec/registration-inputs.md).
- [Pinned native JVM execution and the transition boundary](spec/native-runtime.md).
- [Native construction dependency gate and remaining bootstrap](spec/native-construction.md).
- [Native material-property kernel and its restricted bootstrap scope](spec/material-properties.md).
- [Native material registries and fluid registration queue](spec/native-registries.md).
- [Native fluid construction and isolated Susy-Core producers](spec/native-fluids.md).
- [Native event dispatch and material registration lifecycle](spec/native-events.md).
- [Native material construction, properties and producer qualification](spec/material-construction.md).
- [Native ore prefixes, marker colors and Groovy type interaction](spec/native-prefixes.md).
- [Native Forge registry identities and state](spec/native-forge-registries.md).
- [Native fluid stacks, NBT and default identity rebinding](spec/native-fluid-stacks.md).
- [Cleanroom-native blocks, WATER/LAVA and enchantment identities](spec/native-identities.md).
- [Material producers sharing native Cleanroom fluids, delegates and events](spec/native-material-context.md).
- [Complete GT catalog, native configuration and the frozen material checkpoint](spec/material-catalog.md).
- [Native item identities, ore membership and GT unification](spec/native-items.md).
- [Native material-prefix generation, registration and family boundaries](spec/material-items.md).
- [Generated material blocks, ItemBlocks and composed family checkpoints](spec/material-blocks.md).
- [Native ore variants, host stones and distinct drop selections](spec/material-ores.md).
- [Developer-authoring value checkpoint and remaining source-feedback boundary](spec/authoring-value-checkpoint.md).
- [Native material-program preflight and its delivery boundary](spec/material-program-preflight.md).
- [Native RecipeMap metaclass extensions, construction and saved-edit feedback](spec/native-recipe-map-extensions.md).
- [Pack material dependencies, native helper execution and incomplete composition](spec/pack-material-context.md).
- [Complete ChangeFlags execution and native mutation behavior](spec/native-material-mutations.md).
- [Native custom items, component declarations and bounded generated-item coexistence](spec/native-custom-items.md).
- [Retained loader and registration research](spec/registration-research.md).
- [Architecture](spec/architecture.md) and [long-term interfaces](spec/interfaces.md).
- [Source construction](spec/recipe-construction.md) and
  [behavior/parity requirements](spec/behavior-and-parity.md).
- [Java implementation and build](jvm/README.md).
- [Thin Workbench integration](integration/README.md).
- [Tests and source conformance](tests/README.md).
- [Source provenance](sources/README.md), [lock](sources/supersymmetry.lock.json)
  and [upstream notices](sources/NOTICE.md).

Contributor coordination, credentials, downloaded source checkouts and generated
state remain private/ignored. Public specifications describe the product, not
agent execution instructions.
