# Cleanroom platform profiles

CleanroomMC is mandatory for active Workbench construction.

Workspace Home discovers the canonical generic-mod fixture through the admitted
`workbench.workspace_home_fixtures` API-1 Cleanroom extension. The profile owns
the fixture root, lock and schema validation, complete-tree check, source input
witnesses, and read-only Gradle/Java preflight. Home retains the profile's
validated owner record and blocks fixture execution when those inputs drift.
The profile's build input digest also binds its Python implementation and CLI
entry point, so a retained Home cannot authorize changed owner code.

The active provisional native runtime is `0.6.12-alpha`, selected by
`provisional.yaml` and the stable-named `runtime-toolchain.json`. The latter
locks the actual platform JAR and CleanMix `0.7.2` plus its runtime dependencies.
It grants no runtime qualification. This version satisfies the newer Fugue
platform requirement in Supersymmetry `0.1.16.15`.

`jvm-runtime.json` owns Axiom's exact Temurin 25.0.4+7 Linux x86_64 runtime
file inventory and build-compiler identity. Its archive selection must agree
with `provisional.yaml` and the existing toolchain provisioner. Axiom embeds
this policy and executes on the actual JVM; it no longer models JVM internals.
Temurin 25.0.4+7 is the deliberately selected Workbench MVP runtime, with unchanged
byte pins. The policy records `selectionStatus: selected-workbench-mvp-runtime`
and `qualificationStatus: unqualified-installed-native-runtime`. Fresh Core-managed
Linux setup and installed CLI/IDE native material/item/fluid startup checks pass
with these exact JVM bytes. This bounded acceptance does not promote the policy's
overall qualification label: native recipes and final MVP qualification remain open.
This Workbench choice does not identify an upstream
pack-installed JRE or change the Cleanroom platform's `cleanroom-provisional`
variant and status.

Axiom's installed `checks materials setup --prepare` flow uses Core's shared
artifact store to acquire the platform's exact raw SERVER inputs and preserve
their original relative paths. Without an engine it returns `inputs-prepared`.
With `--engine-archive` or `--engine-home`, Axiom assembles a Core-managed runtime
from packaged sources and freshly scanned original inputs. Ready setup is reused
by ordinary checks without repeating engine/runtime/JVM paths. Explicit engine/runtime setup
can reuse Core's saved Java selection and still verifies the exact profile bytes.

`registry-runtime.json` separately binds original registry, naming, color and NBT
utility bytecode for Axiom. NBT compound/list aliases and copies execute in those
actual classes; Minecraft binaries are provided separately and never bundled in
the engine. These untransformed utilities do not qualify a pack bootstrap or
the installed transformer composition.

`native-identity-runtime.json` binds separately supplied Minecraft/Cleanroom
inputs, exact external transformed images and their library inventory for Axiom's
[native identity checkpoint](../../../modules/axiom/spec/native-identities.md).
It constructs actual blocks, enchantments and WATER/LAVA using the selected
Cleanroom transformations and a checked native constructor prefix. It does not
launch a game, complete vanilla bootstrap or qualify installed pack composition.

The repository separately retains an exact experimental `0.6.8-alpha` candidate
under `candidates/`. Its evidence is historical and does not transfer to the
active native runtime. The exact
candidate records Minecraft, Cleanroom, Forge, MCP mappings, release digest,
source revision, a generated static worldgen hook catalog, and a compiling
source fixture. It is not labeled stable: the bounded dedicated-server slice
has passed, but integrated-client behavior, production telemetry, broader
catalog reachability, and repeated use beyond this fixture remain open.

The candidate catalog is exact static evidence for its source revision. It
contains 40 hook rows and 21 separately modeled terrain-event rows; the
retained runtime plan exercises only 14 hooks, all of which reached in each of
three completed observer-on cases. Its supported APIs and invasive observation
points must not be generalized to a different Cleanroom build or transformed
runtime.

The provisional profile locks the official Cleanroom client archive,
server installer, Packwiz Installer `v0.5.14`, and Eclipse Temurin
`25.0.4+7` by exact acquisition and identity fields. The historical guarded bootstrap
path verified and extracted the older client archive, provisioned the exact Java
runtime, accepted a real Supersymmetry Packwiz payload, and reached an
overlay-assisted FML client-loaded checkpoint. That disposable client proof is
bounded evidence, not stable compatibility; the canonical materialization and
pack checkout remain unchanged.

The [world-generation influence surface](candidates/0.6.8-alpha/WORLDGEN-INFLUENCE-SURFACE.md)
now separates construction seams, generator-emitted compatibility events,
framework-owned dispatch, and invasive observation. Its additive errata
overlay records omissions and corrections without modifying the
identity-bearing V1 catalog. A buildable generator prototype now owns a
complete provider/generator path, compact chunk sampling, mega-region fields,
and a haloed Priority-Flood/D8 watershed engine while emitting structured
diagnostics and custom JFR events. Its direct fixed-seed dedicated-server
smokes passed locally; those ignored logs are development evidence, not an
admitted runtime or third-party compatibility receipt. Pack-specific BOP, Cave
Generator, and terrain interpretation remain in the Supersymmetry profile.

The candidate now also has a separate
[`transformer-toolchain-lock-v1.json`](candidates/0.6.8-alpha/transformer-toolchain-lock-v1.json)
that preserves candidate-lock V1 verbatim while binding 11
transformation-critical artifact identities and the exact Cleanroom Mixin
Doctor policy. It remains a static specification: Java vendor/patch,
supporting Foundation library bytes, installed presence, runtime service
selection, and final transformed outputs require later receipts.

The implemented [Mixin observability architecture](../../../docs/architecture/MIXIN-OBSERVABILITY.md)
combines a product-generic static scanner, exact-profile Doctor evaluation,
CleanMix service/audit import, a generic runtime-service receipt, and a typed
Crucible transformation ledger. Reported service selection and independent
provider enumeration are deliberately separate; neither proves final
transformed bytes. The static Doctor keeps target/injection decoding,
relocation provenance and process-only development properties as explicit
coverage gaps, so unresolved coverage forces review. An additive Doctor V2 can
now bind a caller-declared complete dependency/class-header closure and make
terminal plugin/connector resolution and inherited-interface decisions without
changing V1 identities. Runtime connector output and actual class loading
remain outside static custody.

The separate packaged AP-conformance verifier binds exact CleanMix `0.7.0`
annotation semantics and compares CLASS-retained class/member declarations
with `cleanmix_version_compatibility.json`. It catches stale or partial output;
it does not prove annotation-processor execution, compiler reproducibility, or
runtime resource selection.

The separate compiler/AP build-custody receipt binds a caller-declared single
compiler invocation: exact processor artifact and service providers, compiler
and runtime identities, ordered compiler and processor options, diagnostics,
source and auxiliary inputs, mappings, refmap, and generated compatibility
metadata. It is an auditable chain of supplied inputs and retained outputs, not
process attestation, source-to-output causality, or reproducible-build proof.

The versioned
[`GroovyScript 1.4.3 platform profile`](groovyscript/groovyscript-1.4.3-v1.json)
supplies Pack Program Studio with the exact preInit/init/postInit order,
reloadability, preprocessor surface, and known class-cache identity boundary.
The separate
[`language-service profile`](groovyscript/groovyscript-1.4.3-language-service-v1.json)
locks the exact GroovyScript JAR/source revision, TCP/LSP compiler semantics,
runtime layout, diagnostic canary, and resource bounds used by
`workbench groovy check`. It supports source-bound canonicalization
diagnostics, but the upstream endpoint cannot authenticate itself as the
inventoried runtime. The separate
[`managed-session profile`](groovyscript/groovyscript-1.4.3-prism-managed-session-v1.json)
locks Prism receipt admission, checked JVM/port overlays, readiness and shutdown
bounds, the WSL/Windows port-and-URI bridge policy, and the sequential
terminal/IntelliJ/VS Code handoff used by `workbench groovy session`. Script
execution, transactional registry reload, and cold/reload/reload effective-state
comparison remain separate work.

Worldgen runtime custody follows the
[`Worldgen Observatory` architecture](../../../docs/architecture/WORLDGEN-OBSERVATORY.md).
Candidate-owned pre/post probe hashes remain stage-local health evidence.
Foundation retains each process's exact final LaunchWrapper class bytes for
actor binding. Cross-run comparison uses a separately pinned semantic
fingerprint that rewrites only a valid `MixinMerged.sessionId` UUID; every
other byte is preserved or the comparison is rejected. The semantic
fingerprint never replaces exact profile or byte custody.

The retained exact-runtime proof is
`crucible-worldgen-observatory-proof:sha256:ec76fc1927a1058760de33a3812db46b2b268e0a374e061bbf52536fff68e503`.
It passed on a dedicated server with all 14 planned probes reached, zero drops,
balanced spans, observer-on/off neutrality, A/A and route-order comparisons,
crash-residue rejection, and bounded Atlas attribution. These are fixture
facts, not a full-determinism or production-readiness claim.

The separate [CleanMix handler-order fixture](fixtures/cleanmix-handler-regression/README.md)
has executed in fresh dedicated-server JVMs for candidate and historical
controls. X02 showed that its no-op native-chain first-entry order is not a
delegated-application oracle: both epochs directly applied parent before child
and the named historical defect was not reproduced. The completed bounded M2
result preserves that limitation and does not authorize general compatibility.

No Recurrent Complex artifact was part of the completed M1 proof. A later
pack-owned stock-artifact exercise observed one independent RC
natural-decoration path and an observer-neutral selected-region result, while
also finding four bundled resource-load failures. The platform profile still
defines no Recurrent Complex integration, adapter, reflection, or
package-specific probe; the exact interpretation belongs to the Supersymmetry
profile.

## Promotion loop

1. Provision a current Cleanroom development workspace.
2. Record the actual loader, build provider, mappings, Java, bytecode, Mixin,
   client, and server identities.
3. Compile one real Supersymmetry vertical slice.
4. Launch disposable client and server smoke fixtures.
5. Fix crashes and incompatibilities and add regression checks.
6. Promote the exact profile to stable after repeated successful use.

The product position remains in
[`docs/product/CLEANROOM-PLATFORM.md`](../../../docs/product/CLEANROOM-PLATFORM.md).
