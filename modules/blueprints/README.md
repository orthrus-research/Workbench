# Blueprints

Blueprints turns an explicit developer request into a reviewable source
change. It selects a registered standard or a clearly labeled experimental
pattern, plans deterministic edits, validates the candidate, and applies the
result through a recoverable transaction.
Core performs the physical source staging, replacement, deletion, and guarded
rollback for a consented direct application. Core also secures the retained
transaction record directories before Blueprints publishes its V1 journal and
history bytes through the filesystem port. Core also creates and holds the M2
transaction lock at its historical path with its V1 token bytes. Blueprints
decides whether an interrupted lock is live or should be quarantined, then asks
Core to remove the exact stale marker. Core also reads and removes the V2
attempt journal and prepared receipt at their historical paths after Blueprints
decides an attempt is complete. The resumable session holds its
historical exclusive marker through Core, so older Blueprints processes still
exclude a concurrent writer. Blueprints retains plan admission and source
semantics.
For Cleanroom fresh-project V2, Core also registers the selected bootstrap
state root and publishes, reads, revises, and removes its historical journal
and retained receipt. Core physically replaces and restores the reviewed Git
exclude bytes and creates and removes the exact bootstrap marker at their
historical paths. Blueprints decides the V2 state transitions and recovery
admission. Core records a Git initialization intent before creating an absent
target or running Git, and retains the initialized target identity. An
interrupted or failed new initialization remains at its target with the V2
journal for review; whole-tree bootstrap cleanup still needs proof of exact
ownership and process absence. Profile-local direct apply still requires a
Core-composed entry route for that state custody.

For native package development, install `api/` and `modules/blueprints/` in a
dedicated virtual environment. Dependency declarations in `pyproject.toml`
replace the former module-local requirements file; see the
[native package guide](../../docs/architecture/NATIVE-PACKAGES.md).

Blueprints owns construction only. Atlas supplies bounded knowledge, pack
profiles supply profile policy, and Crucible supplies runtime evidence. A
successful plan does not by itself establish profile support, runtime
compatibility, or release readiness.

## Current capability

The V1 engine provides:

- strict standard compilation and registry locking;
- target capture and parameterized planning;
- sealed deterministic synthesis;
- isolated simulation and command execution;
- transactional application, recovery, and rollback;
- verification and reproducible local history; and
- resumable sessions behind one CLI interface.

Blueprints does not ship a generic standard registry. A caller must select an
explicit profile-owned registry and ledger; this prevents one pack's rules from
becoming an implicit universal. Supersymmetry currently exposes one tested
planning-only experimental registry plus separate profile-owned recipe-change
and quest-edge flows. Every target is revalidated against its current source
bytes, identities, anchors, and collisions before application.

## Delivery rules

- Stable standards are reusable construction patterns with focused regression
  coverage.
- Experimental patterns stay profile-owned and are labeled at every entry
  point.
- Failed builds, invalid output, or failed required checks stop application.
- Runtime behavior is reported only when a separate observed run supports it.
- Versioned V1 records retain their original identity and meaning.

Current Supersymmetry examples are indexed in the
[profile examples](../../profiles/packs/supersymmetry/blueprints/examples/README.md).

## Layout

- `contracts/`: versioned engine and interface semantics.
- `schemas/`: machine-verifiable request, plan, run, and proof records.
- `examples/`: generic contract examples.
- `src/workbench_blueprints/`: planner, simulator, transaction, and CLI
  implementation.
- `tests/`: engine and profile-boundary regression tests.
- `tools/run_engine_conformance.py`: synthetic engine conformance check.

Standards, templates, allocation ledgers, and action policy belong to the
profile that owns them under `profiles/`. Synthetic compiler fixtures remain
non-authoritative tests. Generated candidates, captured targets, sessions,
review evidence, and build output belong under ignored `.workbench/blueprints/`
storage.

Run the module tests from the repository root:

```bash
python3 -m unittest discover -s modules/blueprints/tests -p 'test_*.py'
```
