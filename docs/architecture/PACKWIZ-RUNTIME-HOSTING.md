# Packwiz runtime hosting

Packwiz is Workbench's manifest and distribution format for files inside a
Minecraft instance. It is not the authority for Cleanroom, Java, launcher
metadata, JVM arguments, runtime admission, or observed game behavior.

The saved-developer-check preparation path lives in Workbench Core, composed by
Shell from packaged profile requirements. See [native environment preparation](NATIVE-ENVIRONMENT-PREPARATION.md)
for the current IDE-first workflow. Existing runtime research/materialization
commands below remain separate surfaces; their implementation is not a claim
that the saved-check path supports every research lane.

## Authority boundary

| Concern | Authority |
| --- | --- |
| Minecraft, Cleanroom, Java, mappings, bootstrap, JVM, launcher, and side identity | Exact platform profile |
| Pack revision, compatibility policy, configuration, and release expectations | Selected pack profile |
| Mods, configs, scripts, resources, URLs, and hashes inside one distribution | Packwiz source and index |
| Packwiz and installer binaries | Exact platform artifact lock |
| Planned writes and resolved identities | Workbench runtime plan and receipt |
| What the assembled game did | Retained observation interpreted by Atlas |
| Whether a construction pattern is admitted | Blueprints |

Workbench receipts bind these authorities; they do not replace them. A pack
profile such as Supersymmetry must always be selected explicitly.

## Runtime model

A runtime has three distinct forms:

1. A **snapshot** binds exact platform, pack, payload, tool, bootstrap, side,
   launcher, and policy identities.
2. A **materialization** is a client or server directory created from that
   snapshot.
3. An **observation** records what happened after a materialization was
   launched.

The distinction matters because Minecraft mutates its instance. Logs, worlds,
caches, options, and generated configuration do not become canonical merely
because they appeared during a run. Managed inputs remain verifiable; mutable
outputs are retained or discarded under an explicit run policy.

```text
platform profile ----+
pack profile --------+--> runtime plan --> materialization --> observation
Packwiz payload ------+
artifact locks -------+
```

## Source modes

Workbench accepts two Packwiz source modes:

- A local Git workspace is authoring input. Workbench copies tracked bytes to
  ignored staging, records excluded untracked files, refreshes the copy, and
  validates the resulting index. It does not silently repair the checkout.
- A published distribution is immutable input. Its declared index and served
  bytes must already agree.

One materialization has one Packwiz payload and one installer state. Workbench
combines that payload with an independently verified Cleanroom base; it does
not layer several Packwiz packs into an implicit overlay.

`pack.toml`, `.packwizignore`, Packwiz metadata, and managed loose files belong
in the pack's own source repository. Downloaded mods, refreshed staging trees,
installed instances, and receipts do not belong in Workbench source.

## Current commands

The generic client path is split so users can inspect each boundary:

```text
workbench runtime-plan WORKSPACE
workbench runtime-bootstrap WORKSPACE
workbench runtime-java
workbench runtime-materialize WORKSPACE
workbench runtime-launch WORKSPACE --launcher-executable PATH --launcher-root PATH
workbench runtime-observe WORKSPACE --launcher-executable PATH --launcher-root PATH
workbench runtime-diagnose WORKSPACE --receipt RECEIPT
```

Use each command's `--help` output as the option authority. `runtime-plan` is
read-only. Materialization and launch write only to declared managed state and
launcher targets after their preconditions pass. `runtime-diagnose` reads
retained evidence without changing it.

The generic materializer currently owns the verified Cleanroom client path.
`runtime-plan --side server` can describe a server target, but server
materialization is available only through a profile that supplies an explicit
verified adapter. A plan is not a claim that every described side can already
be launched.

Project acquisition is separate from runtime construction:

```text
workbench project acquire PROFILE --destination PATH --source URL
workbench project qualify PATH --profile PROFILE
```

Acquisition obtains declared source; qualification explains which Workbench
operations are admissible for that checkout. Neither command makes the pack
canonical or publishes it.

## Cleanroom and launcher safety

Packwiz does not provision Cleanroom. Workbench therefore verifies the
Cleanroom launcher base or server bootstrap before installing a payload.

For Prism Launcher and MultiMC clients, the materializer enforces these
invariants:

- the selected platform profile supplies the exact Cleanroom and installer
  artifacts;
- Packwiz writes only the Minecraft payload root;
- the installer receives a dedicated empty sentinel as its MultiMC folder, so
  it cannot rewrite the real launcher manifest;
- Workbench hashes `mmc-pack.json` before and after installation and fails on
  any change;
- the platform profile, not a Packwiz `forge` value, carries Cleanroom
  identity; and
- optional Packwiz choices and their resolved tree are recorded in the
  materialization receipt.

Workbench invokes the pinned installer entrypoint directly. It does not let an
installer bootstrapper substitute a newer release. A `--seed` root may supply
only files whose destination and content hash match Packwiz metadata; it never
copies an instance wholesale or bypasses a provider's distribution policy.

Compatibility patches apply only to a disposable launch projection. They do
not alter the canonical materialization and must be named in the launch
receipt.

## Managed and mutable files

Packwiz should manage inputs intended to converge:

- distributed mods and libraries;
- exact pack configuration;
- scripts, resources, structures, and default assets; and
- explicit client-only or server-only files.

It should not index:

- saves, worlds, or player data;
- logs, crash reports, or captures;
- caches, generated configuration, or launcher metadata;
- credentials, tokens, or account data; or
- local developer overrides.

Avoid preserved files in controlled fixtures because they intentionally allow
local bytes to diverge from the manifest. Developer overrides may exist in a
declared materialization, but the receipt must report that drift and must not
claim byte equivalence with a controlled fixture.

Operational state stays outside the public source tree:

```text
.workbench/
  targets/     acquired or selected source workspaces
  artifacts/   content-addressed tools and downloads
  staging/     disposable Packwiz refresh trees
  fixtures/    materialized client and server instances
  evidence/    receipts, logs, captures, and tree manifests
  cache/       regenerable download and index caches
```

## Publication contract

A publishable Packwiz payload must be produced from an exact clean pack
revision. The release process refreshes with a pinned Packwiz binary, rejects
tracked changes, validates indexed paths and hashes, installs supported sides
into empty roots, and computes deterministic managed-tree identities.

Validated bytes may then be served from an append-only static HTTPS path such
as:

```text
https://HOST/snapshots/SNAPSHOT_ID/DISTRIBUTION_ID/pack.toml
```

A mutable channel may point to a snapshot for convenience, but it is never a
release or evidence identity. `packwiz serve` is an authoring tool because it
can refresh on request; it is not an immutable production host.

Workbench does not select a hosting provider. A conforming host must preserve
the validated relative tree, immutable snapshot paths, and ordinary HTTP
delivery. Publication remains outside a local runtime command unless an
explicit release workflow performs it.

## Evidence and experiment admission

A materialization receipt proves construction identity, not game behavior. A
runtime becomes a Crucible experiment only when it also binds a disposable or
explicitly authorized target, initial conditions, actions, checkpoints,
capture methods, retention policy, limitations, and cleanup result.

Crucible retains that experiment and its captures. Atlas interprets observed
evidence. Blueprints decides whether the evidence supports an admitted
construction pattern. An ordinary Packwiz install makes none of those claims.

Related contracts:

- [Crucible runtime service](CRUCIBLE-RUNTIME-SERVICE.md)
- [Crucible graph model](CRUCIBLE-GRAPH-MODEL.md)
- [IDE plugin architecture](IDE-PLUGIN-ARCHITECTURE.md)
- [Runtime diagnosis V2](../../modules/workbench-shell/contracts/runtime-diagnosis-v2.md)
- [Project qualification](../../modules/workbench-shell/contracts/workbench-project-qualification-v1.md)
