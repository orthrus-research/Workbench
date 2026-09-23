# Workbench Shell

Workbench Shell is the surface-neutral orchestration boundary for the terminal,
CLI, VS Code, and IntelliJ Community clients. It carries project context,
capability discovery, review and consent, retained-record references, and
results. It delegates domain behavior to Atlas, Blueprints, Sentinel,
Crucible, Relay, and the selected profiles instead of reinterpreting their
claims.

Workbench Shell is the optional, independently versioned `workbench-shell`
distribution. Its [native manifest](pyproject.toml) owns its version and
dependencies. Core owns dispatch, environment management and service hosting;
Shell composes domain workflows without replacing those owners.

The default developer path is [IDE-first local source review](../../docs/architecture/LOCAL-SOURCE-REVIEW.md).
Developer-authored saved changes do not require Blueprints or construction plans.
Existing authoring workflows use the optional `construction` native package extra.
Saved native material preflight uses the optional `material-checks` extra and
`context run SESSION -- checks materials run` command, which captures current saved
workspace edits afresh (no hand-prepared archive or intent file); see the
[Axiom material-program contract](../axiom/spec/material-program-preflight.md).

## Entry points

Install Core and the optional Shell module through the
[native package path](../../docs/guides/getting-started.md). The checked
[Pixi environment](../../packaging/pixi/README.md) is for source development:

```bash
pixi run --locked --no-config workbench --version --json
pixi run --locked --no-config workbench --help
```

The JSON version output follows the closed
[native Core version](contracts/core-version.md)
contract for both source and installed Core distributions.

The installed `workbench` launcher is the user-facing entry point. Direct
`python3 tools/workbench.py ...` and `python3 -m workbench_shell ...` forms are
source-checkout diagnostics and remain independently runnable.

## Capture recipes from a selected environment

`workbench capture recipes` provides an experimental `plan`, `prepare`, `run`,
`show`, `cancel` and `export` workflow for saved pack source and an explicitly selected
prepared server/JDK. Shell composes Project Intelligence source observations,
Core custody and execution, the pack profile's observer, Crucible admission and
Atlas's complete dead-end audit. Each edit receives a new retained attempt.

The initial Supersymmetry adapter supports its pinned original Forge/Java 8
binary model and allows different saved source branches. Preparation, native
capture admission and Linux/Windows game qualification remain distinct. See the
[developer-local recipe capture contract](contracts/developer-recipe-capture-v1.md)
for commands, exact confirmation IDs, EULA acceptance and coverage limits.

Export a completed attempt with `workbench capture recipes export` to share its
captured observations, graph and saved audit. Recipients use independent
`workbench atlas scans import`, `show` and `audit` commands without a game,
checkout or profile. Stored findings support filtering and paging without
running the evaluator again. See the
[scan exchange contract](../atlas/contracts/atlas-completed-scan-v1.md).

## Setup, configuration, and repair

Setup owns the per-user environment selection. It checks its plan before
changing local state and does not silently select Supersymmetry or another pack
profile:

```bash
workbench setup
workbench setup --check --json
workbench setup --plan --json
workbench repair --check --json
workbench open /path/to/project --json
```

`workbench launcher setup` separately reviews a credential-free Prism or
MultiMC executable/root binding. Setup and repair never read launcher account
data or edit shell startup files. Git, Java, launcher, and workspace choices
remain explicit, and a non-interactive apply requires the exact reviewed plan
ID. A missing account store is reported separately and does not block the
launcher binding; Runtime Launch reports the launcher-owned Quick Setup or
offline-profile step if a launch reaches that boundary.

The suite-root [`workbench.toml`](../../workbench.toml) is the current
configuration document. It selects exact platform and pack profile documents
and may nominate an external Java candidate; it does not own Minecraft,
Cleanroom, dependency, or pack versions. Source-checkout validation is:

```bash
PYTHONPATH=modules/workbench-shell/src:modules/project-intelligence/src \
  python3 -m workbench_shell config validate --json

PYTHONPATH=modules/workbench-shell/src:modules/project-intelligence/src \
  python3 -m workbench_shell config resolve --for runtime-java --json
```

Only `workbench/config/v1` is accepted. There is no legacy-schema reader,
inheritance, `.env` parser, implicit redirect, or secret interpolation. See the
[configuration contract](contracts/workbench-configuration-v1.md),
[User Setup V1](contracts/workbench-user-setup-v1.md), and
[Host Repair V1](contracts/workbench-host-repair-v1.md).

`workbench host-status --json` is the bounded
[Host Adapter V3](contracts/workbench-host-adapter-v3.md) observation. It
reports capability evidence and missing requirements; it does not turn a host
probe into a package or support claim.

## Saved material initialization checks

With Axiom, Shell's `material-checks` extra and the selected profiles installed,
bind the already provisioned engine, native runtime and platform-selected JDK
once for the selected Work Session's developer context:

```bash
workbench context run SESSION -- checks materials setup \
  --context supersymmetry:material-authoring-pack \
  --engine-home /path/to/axiom-engine \
  --runtime-home /path/to/material-runtime \
  --java /path/to/selected-jdk/bin/java

workbench context run SESSION -- checks materials setup-status \
  --context supersymmetry:material-authoring-pack

workbench context run SESSION -- checks materials run \
  --context supersymmetry:material-authoring-pack
```

Replace `SESSION` with the selected Work Session ID and use the context returned
by `checks materials contexts`. Setup retains private Core-owned bindings for
that workspace/profile selection and material context, not a global default.
It validates existing inputs; it does not download or build them automatically.
Profiles own native context and JVM policy; Core owns storage and supervision.

`setup-status` reports `missing`, `ready` or `stale`. `ready` means the selected
engine/runtime bytes, module/profile bindings and JVM files still match; it is
not proof that native initialization completes or that the JVM is qualified for
the intended installed pack. The current Workbench JVM selection remains
provisional. Changed or removed inputs require explicit review and another
`setup` call; cached paths alone never authorize a run.

After saving edits, repeat `run` without the three paths. Each invocation captures
current saved additions, changes and deletions in the complete selected Groovy
and configuration directories and uses a fresh native worker. No expectation
file is required; `--request` adds optional saved expectations. Unsaved editor
buffers are excluded, and stale results must not decorate changed source.
Axiom reports native errors and observed effects; it never repairs source or
launches a Minecraft client/server. A successful material observation does not
imply recipe, gameplay or whole-pack validity.

Setup's `--program-root` defaults to the checkout root (`.`). `run` and `prepare`
reuse that default unless explicitly overridden. Advanced invocations may supply
`--engine-home`, `--runtime-home` and `--java` together; partial overrides are
rejected, and a one-off override does not replace stored setup. The retained
`prepare`/`execute` review flow and exact request confirmation remain available.

## Capability catalog and console

The installed command registry is the live inventory. Documentation and IDE
clients must not maintain a competing command count or availability list.

```bash
workbench capabilities
workbench capabilities runtime --json
workbench console catalog
workbench console wizard
workbench console replay latest
```

The console catalog preserves each owner's authority, risk, option types,
availability, limitations, and review policy. `console run` renders exact argv
without a shell. When an owner supplies a plan, execution requires explicit
consent and the digest-bound review; the owning command still performs its own
freshness and mutation checks.

```bash
workbench console run explorer.search --set query=example:machine
workbench console run runs.managed \
  --set recipe=debug \
  --set profile=supersymmetry
```

Watch, ingest, retained sessions, replay, and JSON Lines output use the same
terminal surface. Private session data stays under
`.workbench/sessions/live-console/`. See the
[console architecture](../../docs/architecture/HIGH-SIGNAL-LIVE-CONSOLE.md)
and [command-binding contract](contracts/workbench-live-console-command-binding-v2.md).

Process Studio's current Shell route compares two exact observed-effect
snapshots. An optional declared envelope evaluates the comparison against its
bounds; neither form proves causality, playability, construction approval, or
save compatibility.

```bash
python3 tools/workbench.py process effects compare \
  --baseline .workbench/evidence/baseline.json \
  --candidate .workbench/evidence/candidate.json \
  --envelope .workbench/evidence/change-envelope.json \
  --fixture-adapters \
  --output .workbench/evidence/comparison.json \
  --json
```

The built-in fixture adapters are synthetic and require the explicit flag.
Profile-specific compatibility experiments and diagnostic patterns remain
owned and selected by their profile; this generic comparison does not replace
them.

## Project, construction, and runtime commands

The source module exposes the following current command families. Run
`python3 -m workbench_shell COMMAND --help` with the source `PYTHONPATH` above,
or use `workbench capabilities`, for exact arguments.

| Commands | Boundary |
| --- | --- |
| `inspect`, `initialize`, `register` | Inspect a project, select an exact installed Cleanroom instance, and run profile-backed registration plans. |
| `blueprint-stage`, `material-fluid`, `feature` | Stage or review owner-produced feature changes and carry an exact reviewed material/fluid plan through a disposable client. |
| `runtime-plan`, `runtime-bootstrap`, `runtime-java` | Plan a disposable runtime, verify its Cleanroom base, and select or provision profile-owned Java. |
| `runtime-materialize`, `runtime-launch`, `runtime-observe` | Populate, launch, and retain evidence from a Workbench-owned runtime projection. |
| `runtime-diagnose`, `runtime-worldgen-audit` | Read retained launch, log, artifact, and profile-selected world-generation evidence. |
| `runtime-worldgen-fingerprint`, `runtime-worldgen-compare`, `runtime-worldgen-block-delta` | Compare identity-bound observations of stopped Anvil worlds. |

Planning commands do not mutate the source or an installed game instance.
Commands that write require explicit targets and retain their receipts under
ignored state. Seed roots are read-only and contribute bytes only when exact
profile- or Packwiz-declared paths and hashes match.

The selected profile remains authoritative for Cleanroom, Minecraft, Java,
pack artifacts, runtime diagnostics, and any compatibility experiment.
Workbench Shell supplies orchestration and storage; it does not generalize a
Supersymmetry-specific result into platform policy.

## Service and client boundary

Workbench has two distinct protocol surfaces:

- The installed Service V3 path uses an owner-private authenticated local
  endpoint and durable store. It supports context-bound Feature Studio work,
  progress/event replay, cancellation at declared safe points, restart
  recovery, and exact result reopening. `workbench feature-service --help`
  exposes the explicit endpoint/credential client commands.
- `python3 -m workbench_shell serve` is the retained JSON-RPC 2.0 stdio host
  for Client Protocol V2 project inspection, runtime planning, and diagnosis.
  V2 has no durable-job or Service V3 semantics.

See the [installed service lifecycle](contracts/installed-service-lifecycle-v3.md),
[Feature Studio Service V3 contract](contracts/feature-studio-service-v3.md),
and [Client Protocol V2 contract](contracts/client-protocol-v2.md).

The responsibility split is deliberate:

| Layer | Owns |
| --- | --- |
| Setup | Local executable, workspace, profile configuration, Java, launcher, and private-state selections. |
| Catalog and Shell | Discoverability, exact command review, consent presentation, transport, and owner-result routing. |
| Service V3 | Authenticated local transport, context/input binding, durable job custody, events, cancellation, and recovery. |
| Domain modules and profiles | Meaning, construction rules, diagnosis, compatibility inputs, and success claims. |
| Native clients | Editor-native views, forms, progress, diffs, and navigation over installed-Core records. |

The canonical clients are [VS Code](../../clients/vscode/README.md) and
[IntelliJ Community](../../clients/intellij-community/README.md). They invoke
the installed Core directly and do not embed profile policy, package a second
Core, or create an approval path.

## Storage and verification

Downloaded dependencies, managed runtimes, projected clients, worlds,
captures, service state, credentials, sessions, and build output belong under
ignored `.workbench/` storage or another explicitly selected external store.
The source checkout contains contracts and implementation, not generated
evidence.

The module layout is intentionally conventional:

- `src/workbench_shell/`: orchestration, setup, catalog, protocol, and service
  implementation;
- `contracts/` and `schemas/`: versioned public boundaries;
- `conformance/`: retained protocol vectors;
- `data/`: component, capability, and build-path inputs; and
- `tests/`: focused module tests.

Run the focused repository suite with:

```bash
python3 validation/validate.py --suite workbench-shell
git diff --check
```

The Shell boundary is further described by
[Decision 0004](../../docs/decisions/0004-first-class-optional-ide-plugins.md)
and the [IDE client architecture](../../docs/architecture/IDE-PLUGIN-ARCHITECTURE.md).
