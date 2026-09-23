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
