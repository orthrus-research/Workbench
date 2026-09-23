# Project Intelligence

Project Intelligence inventories the exact components that can shape a
Workbench target and records how their configuration and registration topology
was established. Its first contract is deliberately Mixin-focused while the
module boundary remains product-generic.

Project Intelligence does not interpret inventory as final game knowledge.
Atlas remains the interpretation authority, Crucible owns controlled runtime
evidence, and Blueprints decides whether admitted evidence satisfies a
construction gate. A declaration, log line, transformer-chain entry, or
intermediate class image never proves that a transformation completed or that
its effect survived into final runtime state.

## Workspace and profile context

The read-only workspace inspector identifies the canonical workspace root,
Git revision and dirty entries; recognizes a native Packwiz project from
profile-owned markers; reads its identity, loader declarations and index
state; and verifies the binding between separately owned platform and pack
profiles. It records exact profile-document identities in a V2 workspace
context without selecting a runtime, running Packwiz, or mutating the target.
Its currently supported project shape is Supersymmetry Packwiz, not arbitrary
mod and pack layouts.

Run that product-generic inspector independently:

```bash
PYTHONPATH=modules/project-intelligence/src \
  python3 -m workbench_project_intelligence /path/to/supersymmetry \
  --platform-profile profiles/platforms/cleanroom/provisional.yaml \
  --pack-profile profiles/packs/supersymmetry/profile.yaml
```

The inspected project does not need Workbench files. Exact Cleanroom and
Supersymmetry authority remains under `profiles/`.

## Existing-pack qualification

An existing checkout can opt into one explicit pack family without being
copied, renamed, or given Workbench-owned marker files. Project Intelligence
reuses the exact inspector above, presents its identity checks, then records a
content-bound association under Workbench's private user state only after the
developer accepts the reviewed plan:

```bash
workbench project qualify /path/to/Supersymmetry \
  --profile supersymmetry --plan --json
workbench project qualify /path/to/Supersymmetry \
  --profile supersymmetry --apply PLAN_ID --json
workbench project qualify /path/to/Supersymmetry \
  --profile supersymmetry --status --json
```

The interactive form combines planning and a plain `y/N` prompt. IDEs call the
same plan and apply forms, so they cannot bypass the exact plan ID or create a
second profile decision. The binding is refreshed only when qualification-
relevant identity changes; ordinary recipe work remains ordinary pack work.
A dirty tree is disclosed as Git provenance and does not itself change the
qualification state. A mismatched declared Packwiz index hash is attention. A
profile, manifest, loader, platform, or required-marker mismatch is
incompatible and is never saved.

This association answers only which Workbench pack profile applies. It does
not claim that the checkout builds, launches, is supported, is ready to
publish, or has passed release qualification.

## Project acquisition and pull-request preparation

Project Intelligence owns the exact Git identity and workspace-shape checks
used to acquire a declared project. The public flow resolves the profile's
moving `latest` channel before consent, clones into a sibling staging
directory, verifies the resolved commit and required paths, then atomically
publishes the checkout:

```bash
workbench project acquire supersymmetry \
  --channel latest \
  --destination /path/to/Supersymmetry
```

This registered public action is `project.acquire`; inspect its exact risk and
limitations with `workbench capabilities acquire --json`.

In a non-interactive shell this prints a plan and makes no changes. Apply that
exact plan with `--apply PLAN_ID`; an interactive terminal instead asks for a
short plan-specific confirmation. Acquisition authority is the pack-owned
[`acquisition-v1.json`](../../profiles/packs/supersymmetry/acquisition-v1.json),
not a universal Supersymmetry assumption in the shell.

Acquisition Plan V1 remains verbatim for compatibility callers and still
requires an existing regular destination parent. The public command uses the
additive `workbench-project-acquisition-plan-v2`, which binds the exact parent
and a `create` or `reuse` action while keeping planning read-only. Apply
revalidates the nearest existing regular non-symlink ancestor, creates any
reviewed missing parents, and removes only exact empty operation-created
directories if acquisition fails. A successful V2 apply emits
`workbench-project-acquisition-result-v2`; the profile and retained acquisition
receipt remain V1.

Remote pull-request freshness is a separate, explicit write boundary. The
`review.pull-request` action composes provider-bound preparation with recipe
review while preserving the two owners:

```bash
workbench review pr 2002 \
  --profile supersymmetry \
  --source /path/to/Supersymmetry \
  --plan --json

workbench review pr 2002 \
  --profile supersymmetry \
  --source /path/to/Supersymmetry \
  --apply PLAN_ID --json
```

The first command never fetches or writes refs. The second accepts only the
freshly re-observed plan identity, retains immutable Workbench refs, and runs
the bounded historical recipe-delta review without switching branches. Use
`workbench capabilities "prepare pull request" --json` to inspect the current
boundary.

The advanced split form prepares one PR once, then reviews its immutable local
receipt without further network access or checkout mutation:

```bash
workbench review prepare-pr 2002 \
  --profile supersymmetry \
  --source /path/to/Supersymmetry

workbench review recipes \
  --profile supersymmetry \
  --source /path/to/Supersymmetry \
  --prepared-receipt /path/printed/by/the/first/command.json
```

Provider-bound preparation uses the
[Supersymmetry-owned GitHub provider profile](../../profiles/packs/supersymmetry/github-pr-provider-v1.json)
to observe and bind the PR URL, state, original base/head object IDs, and
provider merge identity before any Git write. Apply observes that provider
projection again before and after fetching the exact objects, then retains
immutable Workbench refs and a
[`workbench-pr-preparation-receipt-v2`](schemas/workbench-pr-preparation-receipt-v2.schema.json)
under external Workbench state. Planning binds one canonical physical state
path and rejects symbolic-link, junction, and other Windows reparse components;
apply revalidates that custody immediately before creating receipt state.
Recipe Review verifies V2 entirely offline and compares the retained provider
base to retained provider head, recovering a historical PR delta even when the
head is already contained in today's target.

The provider-bound V2 receipt is the sole supported pull-request preparation
record. It is consumed without fetching, switching branches, running hooks or
filters, hydrating LFS, or depending on the checkout's current branch. The
library-level `provider_metadata_path`/observer seam admits bounded deterministic
test fixtures; it is not exposed by the command line. The public V2 command
always observes the profile-declared GitHub API.

## Local Git tree observation

The `git_tree` primitive resolves an explicit local commit-ish once and
temporarily projects one candidate-relative subtree from verified raw Git blob
objects. It is the observation seam behind `workbench review recipes
--baseline-ref` and `--pr-base`: Pack Program Studio still owns Groovy and
recipe analysis. Exact-ref mode materializes the resolved commit itself.
PR-base mode pins local target tip and candidate HEAD, requires one unambiguous
merge base, materializes that commit, and exposes bounded committed changed
paths across the repository and under the selected root. Both Git modes also
partition bounded dirty-status paths into selected and excluded scope for
truthful human disclosure; those observations are not added to Pack Program
Studio V1. Candidate HEAD and working-tree status are rechecked after the
consumer finishes so a moving review target fails closed. It never claims the
local target ref is fresh relative to a remote.

The materializer does not fetch, checkout, invoke archive attributes, run
hooks or content filters, hydrate Git LFS, or initialize submodules. It rejects
symlinks, gitlinks, LFS pointers, non-portable paths, object-ID mismatches, and
profile-exceeding file or byte counts in the selected subtree. The temporary
projection is removed at context exit; the candidate working tree, index, and
refs are not modified.

## Saved working-tree inputs

`working_tree.capture_source_inputs(checkout)` retains exact tracked and
non-ignored untracked file bytes and modes, plus a sorted, immutable
`SourceInputs.deleted_paths` tuple for indexed files observed missing. Two
matching source-observation passes are required before any of these values
are returned. A deletion, restoration, or saved change between the passes
rejects the capture. Consumers use this retained tuple directly instead of
asking Git for missing filenames after the capture.

`saved_candidate.candidate_manifest(inputs)` and
`saved_candidate.stage_candidate(inputs, destination)` retain their existing
V1 manifest and identity. The deletion tuple is supplemental metadata: the
observation's `file_count` equals present files plus these missing indexed
files. A staged deletion is already absent from the observed index and count;
it is represented by the index and source identities, not by this tuple.
Existing two- and three-argument `SourceInputs` construction remains supported
with an empty tuple by default. Callers needing complete deletion metadata
must use `capture_source_inputs`.

## Initial contract surface

- [Workspace doctor report v1](contracts/workspace-doctor-report-v1.md)
  defines the first product-facing read-only project-context and readiness
  envelope. Its generic implementation discovers bounded Gradle, Git,
  Java-declaration, source/resource, configuration, Groovy, Mixin-config, and
  mod-descriptor surfaces. Exact Cleanroom and pack checks are composed from
  profiles; they are not embedded here.
- [Mixin component topology receipt v1](contracts/mixin-component-topology-receipt-v1.md)
  defines exact artifact and contained-component identity, configuration and
  registration topology, behavior-compatibility epochs, bounded findings, and
  evidence states.
- [Mixin component topology receipt schema v1](schemas/mixin-component-topology-receipt-v1.schema.json)
  provides the JSON Schema Draft 2020-12 envelope.
- [Mixin dependency closure receipt v1](contracts/mixin-dependency-closure-receipt-v1.md)
  is a compatible supplement retaining caller-declared artifact roles and
  edges, exact class headers, and deterministic resolution paths without
  changing topology V1 identity semantics.
- [Mixin AP compatibility conformance receipt v1](contracts/mixin-ap-compatibility-conformance-receipt-v1.md)
  is an additive packaged-output check that reconstructs class defaults and
  explicit member overrides from CLASS-retained annotations and compares them
  with the exact packaged compatibility metadata.
- [Mixin compiler/AP build receipt v1](contracts/mixin-compiler-ap-build-receipt-v1.md)
  is an additive invocation-custody record for exact compiler/runtime identity,
  processor artifacts and service entries, ordered options, diagnostics,
  sources, inputs, and generated Mixin outputs. It explicitly does not claim
  process attestation or reproducible-build proof.

The implemented offline scanner reads exact regular, non-symlink JAR/ZIP
files without loading their code. It handles strict ZIP member safety,
manifest continuation lines, Mixin configs and declared classes, config-plugin
interface headers, refmaps, CleanMix compatibility metadata, registration
routes, embedded transformation-adjacent namespaces, and static owner-ID
collision inputs. Every receipt and child record is content-addressed; the
semantic validator additionally recomputes producer policy, artifact/config
set digests, endpoint types, evidence admission, references, ordering, and
derived completeness counts. Re-hashing a forged runtime state or an exact
finding backed only by missing evidence does not make it admissible.

```bash
python3 tools/inspect_mixin_artifacts.py --output .workbench/mixin-topology.json mod.jar
```

The exact CleanMix conformance profile can be applied with:

```bash
python3 tools/inspect_mixin_ap_compatibility.py \
  --policy profiles/platforms/cleanroom/mixins/cleanmix-ap-compatibility-policy-v1.json \
  --output .workbench/mixin-ap-compatibility.json mod.jar
```

An exact compiler/AP custody set can be assembled from a strict V1 input
specification with:

```bash
python3 tools/assemble_mixin_compiler_ap_build_receipt.py \
  --policy profiles/platforms/cleanroom/mixins/cleanmix-compiler-ap-build-policy-v1.json \
  --output .workbench/mixin-compiler-ap-build.json \
  .workbench/mixin-compiler-ap-build-input.json
```

Target annotations, injection-point overlap, relocated dependency identity,
runtime plugin decisions, and transformation completion remain outside this
static scanner. The compiler/AP receipt binds caller-declared bytes but does
not prove source-to-output causality or reproducibility. Dependency closure is
explicit input: unlinked adjacent JARs
are not searched, and `complete` remains a caller assertion. The exact
Cleanroom profile consumes its facts through the
[Mixin Doctor architecture](../../docs/architecture/MIXIN-OBSERVABILITY.md).

The receipt can bind the existing Cleanroom candidate-lock V1 by exact ID,
format, schema version, and file SHA-256. It does not extend, normalize, or
rewrite that identity-bearing V1 document. Platform-specific discovery rules
and expectations remain under `profiles/`.

Generated inventories, extracted resources, logs, class images, and receipts
belong under ignored `.workbench/` storage or another declared external store.

## Runtime Explorer static surfaces

The product-generic runtime-surface scanner recursively inventories bounded
conventional Gradle module source, resource, configuration, and script roots.
It records exact file hashes and line navigation for classes, constructors,
methods, fields, Mixins and targets, namespaced literals, recipe and loot-table
resources, JSON identities, configuration keys, and Groovy keys. Nested
`mcmod.info` descriptors bind declarations to their closest module owner and
version. The lexical parser masks comments and literals before member parsing;
its output remains `declared` or `static-possible`.

The exact JVM archive surface reads classfiles without defining or executing
them. It exposes class/member descriptors, inheritance and interfaces,
classfile version, line tables, exceptions, references, constant strings, and
resource-domain identities with archive/entry hashes and explicit bounds. The
Explorer projection also exposes validated topology registration endpoints and
phases without claiming runtime activation.
The [Exact Runtime Explorer](../runtime-explorer/README.md) consumes both
surfaces. Neither establishes classpath selection, transformation completion,
registration, or execution.

## Workspace Doctor

The first composed daily-driver preflight is:

```bash
python3 tools/workbench.py doctor --profile supersymmetry
```

It binds the existing Supersymmetry worldgen iteration profile and exact
Cleanroom candidate, reports Java roles separately, inventories and safety-
checks the disposable runtime template, and shows the exact next commands.
The default is read-only; `--output` is the only optional write. See the
[architecture and current boundary](../../docs/architecture/WORKSPACE-DOCTOR.md).
