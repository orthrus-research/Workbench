# Crucible

Crucible is Workbench's runtime-observation and immutable-record mechanics
module. It runs bounded experiments, records what an observer actually saw,
validates caller-supplied closures, and produces deterministic graph artifacts
for Atlas and other read-only consumers.

Crucible does not infer game meaning or authorize a source change. Atlas owns
interpretation, Blueprints owns construction, and profiles own platform- and
pack-specific policy.

## Core model

The [graph model](../../docs/architecture/CRUCIBLE-GRAPH-MODEL.md) combines:

- explicit target and input bindings;
- canonical evidence, admission, revision, and graph record validation;
- deterministic immutable graph shards;
- bounded materialization, impact planning, and equivalence checks;
- durable local job records;
- revision-pinned bounded queries; and
- content-addressed identities over exact immutable inputs.

Crucible does not include a general graph repository, append-transaction
coordinator, publication-proof workflow, moving-reference store, or index.
Integrations that persist or publish the artifacts must supply and validate
those mechanics outside the retained core.

The supported stateful runtime is `workbench_crucible_service`, hosted by
Workbench Shell's Service V3 transport. Versioned graph, context, and job
packages are stable record-format dependencies, not alternate service entry
points. See the [runtime architecture](../../docs/architecture/CRUCIBLE-RUNTIME-SERVICE.md).

Raw logs, worlds, captures, provisioned targets, indexes, and build output must
stay under ignored `.workbench/` storage. Checked-in contracts, schemas,
fixtures, and synthetic tests are the portable public surface.

## Runtime snapshots and Mixin diagnostics

[Composite Runtime Snapshot V2](contracts/runtime-snapshot-v2.md) joins
validated receipts from one exact launch without changing their individual
meaning. It keeps `unavailable`, `not_observed`, and `failed` distinct.

The Mixin record families cover artifact topology, provider discovery,
configuration lifecycle, selected service components, transformer-chain
epochs, application stages, and final class definitions. Intermediate logs or
exported class files are not final-definition authority. Platform-specific
observers and importers live in the Cleanroom profile.

```bash
python3 profiles/platforms/cleanroom/tools/run_mixin_doctor.py --help
python3 modules/crucible/tools/assemble_mixin_transformation_ledger.py --help
```

See the [Mixin observability architecture](../../docs/architecture/MIXIN-OBSERVABILITY.md)
for the evidence boundaries.

## Worldgen development

The worldgen iteration runner composes a source build, a fresh disposable
Cleanroom runtime, one frozen plan, runtime diagnostics, optional Strata state
capture, and an optional same-seed comparison:

```bash
python3 tools/workbench.py worldgen dev --profile supersymmetry
```

Its [iteration report](contracts/worldgen-iteration-report-v1.md) is a local
development record, not a causal proof. For a closed observation, use the
Worldgen Observatory capture/audit/query path instead:

```bash
python3 modules/crucible/tools/run_cleanroom_worldgen_case.py --help
python3 modules/atlas/tools/query_worldgen_observatory.py --help
```

Pack defaults remain under the selected profile. A mod that participates in
normal Forge lifecycle is neither adapted nor implicitly controlled by the
generic runner.

## Managed runs and disposable storage

Pack-owned run profiles resolve into a checked, read-only plan before the
runner receives an argument vector:

```bash
python3 tools/workbench.py run fast --profile supersymmetry --show
python3 tools/workbench.py run fast --profile supersymmetry
```

The runtime manager inventories Workbench-owned storage, creates independent
runtime copies, snapshots stopped worlds, restores into a fresh runtime, and
moves eligible resources to recoverable trash before any permanent purge.
Unknown, external, active, symlinked, or broad-root resources fail closed.

```bash
python3 tools/workbench.py storage list
python3 tools/workbench.py runtime create --profile supersymmetry --label local-test --show
```

The exact semantics are defined by the
[storage inventory](contracts/storage-inventory-v1.md),
[managed runtime](contracts/managed-runtime-v1.md), and
[operation receipt](contracts/storage-operation-receipt-v1.md) contracts.

## Physical state and GTCEu worldgen

The Strata bridge validates an external dense chunk-state capture and its
viewer handoff without making rendered state a causal claim. GTCEu inventory
tools validate exact 1.12.2 definition artifacts, enumerate bounded worldgen
effects, and materialize reviewed overlays only into fresh ignored storage.

```bash
python3 modules/crucible/tools/run_strata_observation.py --help
python3 modules/crucible/tools/inventory_gtceu_worldgen_impact.py --help
python3 modules/crucible/tools/materialize_gtceu_worldgen_overlay.py --help
```

## Layout

- `contracts/`: versioned capture, receipt, graph, and operation semantics.
- `schemas/`: closed machine-readable record shapes.
- `conformance/`: language-neutral canonicalization vectors.
- `src/`: generic capture, storage, graph, service, and integration packages.
- `tests/`: synthetic contract and implementation checks.
- `tools/`: explicit offline assembly, audit, and runner entry points.

Run the module tests from the repository root:

```bash
python3 -m unittest discover -s modules/crucible/tests -p 'test_*.py'
```
