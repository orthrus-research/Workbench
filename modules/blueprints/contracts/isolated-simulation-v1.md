# Blueprints isolated simulation v1

Status: implemented experimental contract

## Scope

This contract takes the exact ready planning
request, plan, sealed candidate, planning evidence, and target manifest; runs
every required validation stage in order; and emits one identity-bound
simulation plus private content-addressed evidence.

Simulation does not release candidate bytes, construct instructions or patches,
reserve allocations, mutate the developer target, write run history, or apply
code. Those remain release/application responsibilities.

## Exact target reconstruction

The target manifest binds separate HEAD, index, and worktree content and modes.
`head_mode` and `index_mode` are nullable independently from the current
worktree `mode`; this is required to reconstruct staged executable and symlink
transitions without loss.

For each simulation worktree, the simulator:

1. verifies the source target still equals the accepted manifest;
2. clones without local object sharing and detaches at the exact 40-hex
   revision;
3. reconstructs the exact index from source-index bytes;
4. reconstructs every tracked, staged, unstaged, deleted, and permitted
   untracked worktree path with no-follow reads;
5. recaptures the isolated target and requires byte-for-byte manifest equality;
6. applies sealed operations only inside that worktree; and
7. deletes the complete disposable simulation root before it returns.

The source target is recaptured after simulation. Any source mutation or race
fails closed.

## Approved environment lock

`blueprints-environment-lock-v1.schema.json` is closed and binds:

- the absolute Bubblewrap executable and its SHA-256;
- disabled networking and a cleared inherited environment;
- command timeout and captured-output limits;
- every executable dependency by logical id, byte size, SHA-256, and
  executable status; and
- exactly one isolated compilation and one existing-test command.

Command executables must be members of their declared locked dependencies.
The cache accepts missing bytes only from an injected approved acquisition
provider, verifies their size and digest before an atomic write, rejects
symlinked cache paths, and mounts cached objects read-only.

Bubblewrap unshares every namespace, exposes the candidate worktree only at
`/work`, exposes exact dependency objects only at `/deps/<id>`, supplies a
private `/tmp`, disables the inherited environment, and does not mount the
developer source or Blueprints sealed store. The host userland required to
start locked tools is read-only isolation substrate; tool and fixture
authority still comes only from the environment lock and admitted standard.

## Required gate execution

Gate ordinals and ids must equal the ready plan's complete required stage list.
Simulation implements the universal stages in this exact order:

1. schema and selected-standard validity;
2. composition, parameter, Atlas-drift, and invariant validity;
3. authorized path and provisional allocation collision checks;
4. byte-identical replanning, rendering, formatting, and sealed synthesis;
5. exact target reconstruction;
6. sealed formatted-byte stability;
7. isolated compilation;
8. target existing tests;
9. generated operation-manifest invariants;
10. generated baseline collision semantics;
11. independent-worktree result determinism; and
12. exact generated placement.

Every selected-standard required gate follows the universal stages. Test
runners resolve only their standard-defined command and exact environment
dependency. Every referenced source, runtime, presentation, integration, and
formed-world fixture must exist in the disposable candidate worktree. Trusted
validation hooks are rehashed and mounted read-only before execution.

The first failed or unavailable stage prevents execution of every successor.
Successors remain in the record as `unavailable`; required stages are never
omitted. A simulation passes only when every gate passes.

## Evidence closure and privacy

`blueprints-simulation-evidence-v1.schema.json` is a closed private record. It
binds the candidate, plan, target, environment lock, reconstructed baseline,
isolated candidate result, cleanup fact, and complete gate evidence.

Each private gate record includes only:

- ordinal, stage id, status, and stable reason code;
- canonical command digest when a command ran;
- exit code and timeout state; and
- bounded stdout and stderr digests.

Its `evidence_sha256` is the SHA-256 of the canonical gate record without that
field. The public simulation copies only ordinal, stage id, status, and this
digest. Neither public simulation nor private evidence contains candidate
bytes, target file bytes, command output, or the sealed candidate locator.

The complete private evidence record is canonical JSON in a mode-0700/0600
content-addressed store. Its local locator is operational metadata, not a
portable proof and not part of the public simulation identity.

## Executable surface

[`simulation.py`](../src/workbench_blueprints/simulation.py) provides:

- `compile_environment_lock` and `environment_lock_sha256`;
- `DependencyCache`;
- `SimulationEvidenceStore`; and
- `Simulator.execute`.

The API requires original intake, choices, target manifest, and planning
evidence so deterministic regeneration can rerun planning instead of trusting
a claimed digest. Simulation has no separate CLI; the public Blueprints
lifecycle CLI exposes this same core without changing its semantics.

## Simulation and release boundary

A `passed` simulation means only that the exact sealed candidate passed
all required gates in its approved disposable environment against its exact
target state. Candidate bytes remain sealed.

Release must independently require the same candidate id, target-state id,
environment-lock digest, complete passing gate list, and unchanged current
authority before release. It may not reinterpret a failed or unavailable
result.
