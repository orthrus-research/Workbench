# Blueprints core and CLI interface contract v1

Status: implemented

Contract ID: `BLUEPRINTS-EXECUTABLE-ENGINE-V1`

This contract exposes the accepted C01–A01 engine through one resumable Python
core and one canonical command-line adapter. The CLI calls the core directly;
it does not reproduce planning, simulation, release, application,
verification, history, or proof rules.

It does not admit a feature standard, provide a Gradle or graphical adapter,
select an implementation from source frequency, or weaken a missing formatter,
hook, dependency, post-check, or isolation authority.

## Shared core boundary

[`interface.py`](../src/workbench_blueprints/interface.py) provides:

```text
AdapterSet(...)
BlueprintsCore(workspace, adapters=...)
BlueprintsCore.init(...)
BlueprintsCore.plan(...)
BlueprintsCore.simulate(...)
BlueprintsCore.generate()
BlueprintsCore.apply()
BlueprintsCore.verify()
BlueprintsCore.history()
BlueprintsCore.export_proof()
dependency_directory_provider(root)
```

Every admitted success or handled phase failure returns the same
`susy-blueprints-interface-result-v1` envelope emitted by the CLI. A
pre-phase core rejection raises its stable `BPI`, `BPP`, `BPX`, or `BPA`
diagnostic; the CLI projects that diagnostic into a `rejected` envelope.
Callback adapters are process-local and never serialized into a request,
plan, candidate, run, proof, or session.

The core delegates authoritative work as follows:

| I01 method | Accepted implementation |
| --- | --- |
| `init` | P01 exact target capture and canonical request compilation |
| `plan` | S01 registry plus P01 planner and sealed synthesis |
| `simulate` | X01 environment compiler and isolated simulator |
| `generate` | A01 passing-simulation release admission |
| `apply` | A01 target compare-and-swap transaction |
| `verify` | A01 target and post-check verification |
| `history` | A01 content-addressed history store |
| `export_proof` | A01 privacy-safe portable proof exporter |

I01 may assemble and persist these accepted records. It may not reinterpret
them.

## Protected resumable session

Each run uses one explicit workspace below:

```text
<target>/.workbench/blueprints/<session>/
```

The session name must be non-empty. Existing symlinks in the workspace path
are rejected. The protected Blueprints root remains excluded from an untracked
target manifest, while any tracked protected path remains invalid.
Only this location is accepted; the interface does not discover, read,
migrate, or fall back to another state location.

The workspace contains:

- a private content-addressed session store;
- one atomically replaced `current.json` digest pointer;
- sealed candidates;
- approved dependency objects;
- private simulation evidence and disposable workspaces;
- release artifacts;
- local history; and
- short-lived interface and application locks.

The closed `susy-blueprints-session-v1` snapshot contains only JSON values.
It binds absolute local configuration, original intake, exact target manifest,
canonical request, optional planning/simulation/lifecycle inputs and results,
the current run, and the last history locator.

Every command:

1. exclusively creates the interface lock;
2. verifies the canonical current pointer and its content digest;
3. validates the closed session, nested target/request/run identities,
   cross-record bindings, event chain, and state closure;
4. checks its admitted predecessor before phase work;
5. delegates to the accepted engine component;
6. stores the complete successor snapshot by content digest;
7. synchronizes the atomic current pointer; and
8. removes the interface lock.

A crash before pointer replacement leaves the previous session current. An
unreferenced content object is not a successor state. A stale, missing,
non-canonical, symlinked, or digest-mismatched pointer fails before a lifecycle
event.

## Persisted event behavior

Successful phase commands append the C01 event for that phase. Handled
planning, simulation, generation, application, or verification failure stores
the accepted failure state and exits with the handled-failure class.

`history` is admitted from every persisted C01 state, appends a `no-op` event,
and records only identities and artifacts available at that state. An
initialized history cannot claim a plan; a planned history cannot claim a
simulation; and neither can claim a release.

Illegal predecessor and output-mode failures occur before the phase and append
no event.

I01 allows a failed simulation to be replanned when deterministic planning
returns the same candidate identity. A different candidate requires a new
initialized session so I01 cannot silently bypass C01 invalidation semantics.

## Canonical CLI

The command adapter is:

```bash
PYTHONPATH=modules/blueprints/src \
  python3 -m workbench_blueprints.cli \
  --workspace <session> <command> [arguments]
```

The commands and external inputs are:

| Command | Required external input |
| --- | --- |
| `init` | target repository and repository ID plus intake JSON or equivalent intent/parameter/output/consent flags; explicit profile registry and ledger paths are required |
| `plan` | planning-evidence JSON; optional choices JSON |
| `simulate` | environment-lock JSON; optional local dependency-source directory |
| `generate` | none |
| `apply` | none |
| `verify` | none |
| `history` | none |
| `export-proof` | none |

Input JSON rejects duplicate keys and non-object roots. Final input symlinks
are not followed. Inline parameters use unique `NAME=JSON` arguments, so their
types are not inferred from shell text. I01 proves JSON and flag intake compile
to the same request and initialized run identities. P01 remains responsible
for canonical intake normalization; X01 remains responsible for environment
and dependency digest validation.

`--dependency-source` is a local-only acquisition adapter. Files are addressed
by locked dependency ID, final symlinks are rejected, and X01 independently
checks exact size and SHA-256 before cache admission. It does not authorize a
network fetch.

## Result envelope and disclosure

Every core or CLI result validates against the closed
`blueprints-interface-result-v1.schema.json` schema and contains:

- command;
- `succeeded`, handled `failed`, or pre-phase `rejected` status;
- exit code;
- current run ID and state when safely readable;
- one command-specific public data object; and
- stable code, location, and message diagnostics.

The CLI writes one canonical JSON object plus one trailing newline. Successful
and handled-failure envelopes go to stdout. Rejected envelopes go to stderr.
No banners, progress messages, Python tracebacks, or nondeterministic
timestamps are mixed into the machine stream.

Command projections are:

- `init`: canonical request;
- `plan`: plan, sealed candidate metadata, and compliant revisions;
- `simulate`: public simulation and gate evidence digests only;
- `generate`: release, selected delivery projection, and compact proof;
- `apply`: application record;
- `verify`: verification and, on pass, target-verified proof;
- `history`: current history manifest; and
- `export-proof`: portable proof and export bundle.

The simulation evidence locator, private command evidence, session snapshot,
configuration paths, sealed bytes, unselected release projections, and local
history locators are not CLI simulation or proof payloads. A generated
delivery is exposed only after A01 release admission.

## Exit classes

I01 fixes these process exit classes:

| Exit | Meaning | State effect |
| ---: | --- | --- |
| `0` | phase succeeded | accepted successor or no-op event persisted |
| `2` | CLI usage or adapter conflict | no phase event |
| `3` | illegal predecessor, output mode, or reinitialization required | no phase event |
| `4` | malformed input, missing authority, integrity failure, or accepted component rejection before a handled result | no fabricated successor |
| `5` | handled plan/simulation/generate/apply/verify failure | accepted failure/no-op state persisted |
| `6` | unexpected internal exception | no traceback or fabricated successor |

Underlying stable `BPP`, `BPX`, and `BPA` diagnostic codes are preserved in a
rejected envelope. Interface-specific diagnostics use `BPI100`–`BPI199`.

## Adapter boundary

`AdapterSet` is the only I01 extension boundary:

- formatter runner;
- trusted render-hook runner;
- approved dependency provider;
- deterministic verification post-checks; and
- an application mutation hook reserved for conformance testing.

The same `AdapterSet` instance shape is consumed by the core and CLI. A missing
required adapter reaches the existing fail-closed P01/X01/A01 behavior.
Supplying a callback does not admit it as a standard or dependency; the
selected standard, trusted asset digest, environment lock, and simulation
still control its use.

Later Gradle and graphical clients must call `BlueprintsCore` and consume the
same result schema. They may translate user interaction into canonical intake
or display result fields, but may not implement an alternate engine path.

## Implementation and proof boundary

The implementation is:

- [`interface.py`](../src/workbench_blueprints/interface.py);
- [`cli.py`](../src/workbench_blueprints/cli.py); and
- [`test_interface.py`](../tests/test_interface.py).

Tests cover all eight commands through both resumed core instances and the
CLI, direct and non-mutating output modes, local dependency acquisition,
canonical output, pre-phase rejection without events, blocked planning retry,
missing-adapter simulation closure, history before planning, session-pointer
tampering, duplicate JSON, and stable exit classes.
