# Supersymmetry profile

Supersymmetry is Workbench's first explicit pack profile. It binds generic
Workbench capabilities to this pack's source layout, versions, policies,
fixtures, and observed evidence. It is never an implicit default for another
pack.

The pack itself and its dependencies are not vendored here. Provisioned
checkouts, runtimes, worlds, captures, and generated indexes belong in the
Core-selected user state location. Existing checkout-local `.workbench/`
records remain available by explicit path.

## Profile targets

[`profile.yaml`](profile.yaml) defines two distinct targets:

- `cleanroom-provisional`: the active experimental construction and
  observation target.
- `legacy-forge-0.1.16.11`: a read-only source lock for exact historical
  comparison and migration work.

Historical Forge evidence is valid only for the locked historical target. It
must not be presented as current Cleanroom runtime behavior.

## Published pack and developer sources

[`release-authority-v1.json`](release-authority-v1.json) identifies the
Supersymmetry GitHub releases feed and pins the reviewed 0.1.16.16 client
download by exact size and SHA-256. The pinned ZIP contains a CurseForge
manifest for Minecraft 1.12.2 and Forge 14.23.5.2860 with pack overrides. Its
tag commit and tree identify the release's repository provenance; the tag
checkout is not itself a prepared Packwiz project. A future release choice
must use the GitHub release asset and verify its bytes before Core retains it.

Developers can explicitly select their own checkout or branch through the
separate [`acquisition-v1.json`](acquisition-v1.json) Git source workflow. Its
`latest` channel is the moving `master-ceu` branch, not the latest published
pack release. A developer source selection must not silently replace a saved
release choice. The release descriptor does not create a Cleanroom source lock
or resolve the released pack's CurseForge dependencies.

After preparing the selected client archive, `workbench pack release inputs
--profile supersymmetry --json` asks Core for a read-only
[input plan](../../../core/src/workbench_core/schemas/workbench-pack-release-input-plan-v1.schema.json).
Core rechecks the saved archive's exact bytes and lists the manifest's
CurseForge project/file IDs, required flags and archive member counts. The plan
has a content identity but no download URLs or verified mod bytes. It does not
install the client or substitute the release for a Cleanroom source lock.

The pack-owned [local input policy](runtime/release-local-input-policy-v1.json)
lets Core review files the user obtained separately. Save an owner-private JSON
document following the [local sources schema](../../../core/src/workbench_core/schemas/workbench-pack-release-local-sources-v1.schema.json).
It names the selected input plan ID, an explicit list of optional IDs to include,
and any available source rows. Each source row supplies one manifest
`project_id`/`file_id` pair, an absolute Linux `local_path`, its matching
`filename`, byte `size` and `sha256:` digest. Missing required files and
selected optional files appear as unresolved; an unselected optional file is
separate. Review it with `workbench pack release local-inputs --profile
supersymmetry --sources /absolute/private/local-sources.json --json`.

Core holds each supplied file and its parent while checking its exact bytes,
rejects redirects, linked files and conflicting `mods/` names, and returns a
path-free [local input plan](../../../core/src/workbench_core/schemas/workbench-pack-release-local-input-plan-v1.schema.json).
The source document and its paths remain local. This read-only review does not
prove that a byte stream belongs to its declared CurseForge file ID, acquire
the files into Workbench, extract the overrides, install a client or qualify a
Cleanroom instance. A later acquisition must recheck the live inputs.
Even `local-byte-set-reviewed` is insufficient to authorize installation until
the file-ID provenance is independently established by a reviewed owner policy.

For an existing Prism/Packwiz instance, Core can review its `mods/` files and
`.index/*.pw.toml` sidecars, then retain matching bytes in the Linux Workbench
state directory. Use the selected release's prepared client ZIP first. The
three commands below review the source, retain the exact reviewed candidate,
and reopen retained bytes later without the Prism directory:

```sh
workbench pack release prism-inputs --profile supersymmetry --mods-root /absolute/Prism/instance/minecraft/mods --json
workbench pack release prism-import --profile supersymmetry --mods-root /absolute/Prism/instance/minecraft/mods --expected-plan-id 'workbench-pack-release-prism-import-plan:sha256:<digest>' --json
workbench pack release prism-reopen --profile supersymmetry --expected-plan-id 'workbench-pack-release-prism-import-plan:sha256:<digest>' --json
```

Use the exact `plan_id` returned by `prism-inputs`. Add `--include-optional
PROJECT:FILE` to both the review and import commands for each optional manifest
file you choose. The import rechecks sidecar assertions, source SHA-1, reviewed
size and SHA-256, and publishes an exact Core-managed tree. A read-only WSL 9p
Prism directory is accepted as a best-effort input observation; the retained
Linux tree supplies stable local byte custody. A changed source requires a new
review. An interrupted stage is preserved for review and cannot be reused.

The returned plan and result omit source paths. A Packwiz sidecar and matching
hash do not prove the bytes' CurseForge project/file identity. Unresolved
required files remain unresolved, and retained files are not installed. Core
uses its stable runtime state root as the shared pack-byte workspace identity;
these bytes are independent of a developer project checkout and are bound to
the selected release and pack policy by the retained plan ID.

Core's `plan_mod_augmentation`, `apply_mod_augmentation`, and
`reopen_mod_augmentation` operations can combine a retained Prism mod Core
tree with the two separately reviewed local JARs declared in the
[version-bound augmentation policy](runtime/release-mod-augmentation-policy-v1.json).
They publish a new exact Core tree under `mods/`, reference and preserve the
prior tree, and record both local file identities without source paths. The
new tree can be reopened after the local sources disappear. This retains
reviewed bytes; it does not establish CurseForge origin or install mods.

The selected 0.1.16.16 client manifest also declares three required file IDs
that the [pack-owned placement policy](runtime/release-resourcepack-input-policy-v1.json)
classifies as resource packs under that input plan:
ShaderTech `851152/6655846`, Black Mesa Transit System `885673/6280168`, and
SuperSymmetry Refreshed `1290857/6927766`. The manifest lists IDs but does not
state their installation directories, so Core uses this version-bound profile
policy and reads the corresponding Prism `minecraft/resourcepacks/` directory.

```sh
workbench pack release prism-resourcepack-inputs --profile supersymmetry --resourcepacks-root /absolute/Prism/instance/minecraft/resourcepacks --json
workbench pack release prism-resourcepack-import --profile supersymmetry --resourcepacks-root /absolute/Prism/instance/minecraft/resourcepacks --expected-plan-id 'workbench-pack-release-prism-resourcepack-plan:sha256:<digest>' --json
workbench pack release prism-resourcepack-reopen --profile supersymmetry --expected-plan-id 'workbench-pack-release-prism-resourcepack-plan:sha256:<digest>' --json
```

Use the reviewed plan ID for import and later readback. Core rechecks each
sidecar and ZIP, retains the ZIPs beneath `resourcepacks/` in a distinct private
Linux tree, and records each size and SHA-256 without source paths in the plan
or result. A read-only WSL 9p directory remains an unqualified source
observation. Its local sidecars and hashes do not establish CurseForge file-ID
provenance, and this candidate import does not install the resource packs or
qualify the released client.

## Contents

- `source-locks/`: exact upstream source and binary provenance.
- `atlas/`: pack-specific source adapters, semantic policies, and observation
  tooling.
- `blueprints/`: pack-owned construction adapters and reviewed examples.
- `runtime/`: pack-specific runtime schemas and adapters.
- `run-profiles/`: named local run defaults.
- `worldgen/` and `subsurface/`: pack-owned worldgen, GTCEu, Strata, and
  subsurface configuration.
- `compatibility/` and `diagnostics/`: version-bounded observations and
  diagnostic policies.

Generic code belongs under `modules/`; a pack-specific exception or default
belongs here and must remain explicitly selected.

## Atlas data

The profile projects exact source and observed records into Atlas without
turning declarations into runtime truth. Its
[semantic adapter](atlas/semantic-projection/adapter-v1.json) binds the current
acceptance fixtures, while the repository-wide Atlas catalog provides the
query surface. Generated indexes and captures remain in user state rather
than checked-in profile publications. The source-span and pack-mutation tools
default to Core's selected `atlas/` state tree; `--source-root`, `--output`,
and `--source-index` can select retained paths explicitly.

The explicitly selected `workbench.recipe_graphs` adapter also projects a
sealed Crucible capture into an Atlas categorical graph for finite GT recipe
inspection. The [finite recipe projection contract](atlas/finite-recipe-projection-v1.md)
defines its required categories, quantity-independent resource identities,
input-matching gaps and limits. It preserves the capture's original bindings;
importing a retained capture does not make it current runtime evidence.

## Blueprints

The profile supplies material/fluid, recipe-change, and quest-edge adapters.
The checked examples are indexed in
[`blueprints/examples/`](blueprints/examples/README.md). A candidate is always
bound to the current target bytes and rechecks identifiers, source anchors,
and collisions before application.

No stable Supersymmetry standard is currently admitted. The profile's `0.1.0`
material-backed-fluid registry is an explicitly selected planning-only
experiment; it cannot qualify release because it declares no release gates.

## Local run profiles

Named run profiles resolve the selected target into a checked plan before
execution:

```bash
python3 tools/workbench.py run fast --profile supersymmetry --show
python3 tools/workbench.py run fast --profile supersymmetry
```

`debug`, `worldgen`, and `performance` add bounded diagnostics. Availability
is defined by the selected profile and local environment; a missing runtime or
toolchain fails preflight rather than falling back to another target.

## Worldgen and subsurface tools

The profile contains the current World Studio plans, GTCEu worldgen inventory
rules, Strata capture defaults, and Subsurface Studio binding. Use fresh
disposable worlds for terrain comparisons:

```bash
python3 tools/workbench.py worldgen dev --profile supersymmetry --no-open
python3 tools/workbench.py subsurface --profile supersymmetry summary
```

These tools retain source plans, artifacts, seeds, mod sets, windows, and
capture identities. A final-state match does not establish generator
causality, and a local successful run does not establish general pack support.

The version-bounded case studies under `worldgen/` document the exact behavior
they inspect. They do not create dependencies or adapters in the generic
runner for ordinary Forge lifecycle participants.

## Provenance and storage

Committed profile data should be compact, reviewable authority: schemas,
policies, source locks, definitions, and small fixtures. Generated graphs,
raw logs, source trees, downloaded artifacts, worlds, screenshots, and proof
outputs remain under Core-selected user state or another declared external store.


## Developer-selected recipe evidence

The [developer recipe capture input contract](atlas/developer-recipe-capture-input-v1.md)
binds an exact saved candidate, including declared deletions, to a selected
admitted runtime. It separates portable content identity from local custody and
preserves the historical branch contract. The initial model retains the exact
Forge/Java 8 artifact tuple; it does not admit arbitrary new binaries or establish
an installed capture workflow. Atlas can independently audit an admitted graph
with `workbench atlas recipes audit-dead-ends GRAPH --json`.
